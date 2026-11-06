#!/bin/sh
# FortyTchat: désinstallation utilisateur, sans sudo.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/tools/uninstall_user.py" "$@"
