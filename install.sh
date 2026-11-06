#!/bin/sh
# FortyTchat: installation personnelle, sans sudo.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3 est nécessaire pour installer FortyTchat.' >&2
    exit 1
fi
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/tools/install_user.py" "$@"
