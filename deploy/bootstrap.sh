#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root" >&2
    exit 1
fi
apt-get update
apt-get install -y ca-certificates curl git sudo gpg debian-keyring debian-archive-keyring apt-transport-https

if ! command -v caddy >/dev/null; then
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
        gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        >/etc/apt/sources.list.d/caddy-stable.list
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    chmod o+r /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
fi
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/0.13.6/install.sh |
        env UV_UNMANAGED_INSTALL=/usr/local/bin sh
fi

if ! id nexacrm >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash nexacrm
fi
install -d -o root -g nexacrm -m 0750 /etc/nexacrm
install -d -o nexacrm -g nexacrm -m 0750 /srv/nexacrm
for directory in releases shared backups; do
    install -d -o nexacrm -g nexacrm -m 0750 "/srv/nexacrm/$directory"
done
if [[ "$#" -eq 1 && "$1" == "--prepare" ]]; then
    echo "Base packages and directories ready. Add production.env and admin.hash, then rerun without --prepare."
    exit 0
fi
if [[ ! -s /etc/nexacrm/production.env || ! -s /etc/nexacrm/admin.hash ]]; then
    echo "Create /etc/nexacrm/production.env and /etc/nexacrm/admin.hash first" >&2
    exit 1
fi
chown root:nexacrm /etc/nexacrm /etc/nexacrm/production.env
chmod 0750 /etc/nexacrm
chmod 0640 /etc/nexacrm/production.env
chown root:root /etc/nexacrm/admin.hash
chmod 0600 /etc/nexacrm/admin.hash

systemctl_path="$(command -v systemctl)"
printf 'nexacrm ALL=(root) NOPASSWD: %s restart nexacrm-public.service, %s restart nexacrm-admin.service, %s stop nexacrm-public.service, %s stop nexacrm-admin.service\n' \
    "$systemctl_path" "$systemctl_path" "$systemctl_path" "$systemctl_path" >/etc/sudoers.d/nexacrm-deploy
chmod 0440 /etc/sudoers.d/nexacrm-deploy
visudo -cf /etc/sudoers.d/nexacrm-deploy

install -m 0644 deploy/nexacrm-public.service /etc/systemd/system/
install -m 0644 deploy/nexacrm-admin.service /etc/systemd/system/
install -m 0644 deploy/nexacrm-backup.service /etc/systemd/system/
install -m 0644 deploy/nexacrm-backup.timer /etc/systemd/system/
python3 - <<'PY'
from pathlib import Path

template = Path("deploy/Caddyfile.template").read_text()
password_hash = Path("/etc/nexacrm/admin.hash").read_text().strip()
if not password_hash or any(char.isspace() for char in password_hash):
    raise SystemExit("Invalid admin password hash")
Path("/etc/caddy/Caddyfile").write_text(
    template.replace("__ADMIN_PASSWORD_HASH__", password_hash)
)
PY
chown root:caddy /etc/caddy/Caddyfile
chmod 0640 /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable caddy nexacrm-public.service nexacrm-admin.service nexacrm-backup.timer
systemctl restart caddy
systemctl start nexacrm-backup.timer

if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
fi

echo "Bootstrap complete. The first push to master starts the application."
