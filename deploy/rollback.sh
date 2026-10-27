#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
    echo "Expected current SHA and previous SHA (or NONE)" >&2
    exit 2
fi
if [[ ! "$1" =~ ^[0-9a-f]{40}$ || ( "$2" != NONE && ! "$2" =~ ^[0-9a-f]{40}$ ) ]]; then
    echo "Expected current SHA and previous SHA (or NONE)" >&2
    exit 2
fi
if [[ "$(id -un)" != nexacrm ]]; then
    echo "Run as nexacrm" >&2
    exit 2
fi

root=/srv/nexacrm
exec 9>"$root/deploy.lock"
flock 9
current="$(git -C "$root/current" rev-parse HEAD)"
if [[ "$current" != "$1" ]]; then
    echo "Refusing rollback: current release moved to $current" >&2
    exit 1
fi
if [[ "$2" == NONE ]]; then
    rm "$root/current"
    sudo systemctl stop nexacrm-public.service
    sudo systemctl stop nexacrm-admin.service
    echo "ROLLED_BACK: no prior release"
    exit 0
fi
previous="$root/releases/$2"
if [[ ! -d "$previous" || "$(git -C "$previous" rev-parse HEAD)" != "$2" ]]; then
    echo "Previous release is missing or incorrect" >&2
    exit 1
fi
ln -sfn "$previous" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
sudo systemctl restart nexacrm-public.service
sudo systemctl restart nexacrm-admin.service
echo "ROLLED_BACK: $2"
