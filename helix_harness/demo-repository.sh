#!/bin/sh
# Publish a small, committed public copy for the registration form.
set -eu
cd "$(dirname "$0")/.."
root="${RSI_PUBLIC_REPOS:-${RSI_WEB_HOME:-$HOME/Library/Application Support/Helix Harness/WebConsole}/public-repos}"
dest="$root/pagination-demo"
mkdir -p "$root"
if [ -e "$dest" ]; then
    echo "Project already exists at $dest"
    exit 0
fi
mkdir "$dest"
cp vendor/rsi/evaluation/repo-python-v1/public/page-settings/repo/*.py "$dest/"
git -C "$dest" init -q --template=
git -C "$dest" add -- app.py settings.py public_checks.py
git -C "$dest" -c core.hooksPath=/dev/null -c user.name='RSI Demo' -c user.email='demo@localhost' -c commit.gpgSign=false commit -qm 'Public pagination example'
echo "Register pagination-demo in Coding workspace."
