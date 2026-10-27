#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
    echo "Expected one commit SHA" >&2
    exit 2
fi
sha="$1"
if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Expected a 40-character commit SHA" >&2
    exit 2
fi
if [[ "$(id -un)" != nexacrm ]]; then
    echo "Run as nexacrm" >&2
    exit 2
fi

root=/srv/nexacrm
exec 9>"$root/deploy.lock"
flock 9
repository="$root/repo.git"
release="$root/releases/$sha"
if [[ ! -d "$repository" ]]; then
    git clone --bare https://github.com/sdmelen/NexaCRM.git "$repository"
fi
git --git-dir="$repository" fetch --no-tags origin master
latest="$(git --git-dir="$repository" rev-parse FETCH_HEAD)"
if [[ "$latest" != "$sha" ]]; then
    echo "SKIPPED: master moved from $sha to $latest"
    exit 0
fi
if [[ ! -d "$release" ]]; then
    git --git-dir="$repository" worktree add --detach "$release" "$sha"
elif [[ "$(git -C "$release" rev-parse HEAD)" != "$sha" ]]; then
    echo "Existing release contains another commit" >&2
    exit 1
fi

uv --directory "$release" sync --frozen --no-dev
set -a
source /etc/nexacrm/production.env
set +a
(
    cd "$release"
    .venv/bin/python ingest.py --dry-run
    .venv/bin/python ingest.py
    .venv/bin/python -c 'from nexacrm.config import load_config; from nexacrm.knowledge_base import get_index_status; assert get_index_status(load_config()).ready'
)
git --git-dir="$repository" fetch --no-tags origin master
latest="$(git --git-dir="$repository" rev-parse FETCH_HEAD)"
if [[ "$latest" != "$sha" ]]; then
    echo "SKIPPED: master moved from $sha to $latest during preparation"
    exit 0
fi

previous=""
if [[ -L "$root/current" ]]; then
    previous="$(readlink -f "$root/current")"
fi
rollback() {
    trap - ERR
    if [[ -n "$previous" ]]; then
        ln -sfn "$previous" "$root/current.next"
        mv -Tf "$root/current.next" "$root/current"
        sudo systemctl restart nexacrm-public.service
        sudo systemctl restart nexacrm-admin.service
    fi
    echo "Deployment failed; previous release restored" >&2
}
trap rollback ERR

ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
sudo systemctl restart nexacrm-public.service
sudo systemctl restart nexacrm-admin.service
for attempt in $(seq 1 30); do
    if curl --fail --silent --output /dev/null http://127.0.0.1:8501/_stcore/health &&
       curl --fail --silent --output /dev/null http://127.0.0.1:8502/admin/_stcore/health; then
        trap - ERR
        echo "DEPLOYED: $sha"
        exit 0
    fi
    sleep 2
done
echo "Health checks did not pass" >&2
false
