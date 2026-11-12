#!/bin/sh
# Diagnostic personnel, sans installation ni changement système.
set -eu
FORTYTCHAT_DIAG_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "${XDG_DATA_HOME:-}" in
    /*) FORTYTCHAT_DIAG_DATA=$XDG_DATA_HOME ;;
    *) FORTYTCHAT_DIAG_DATA="$HOME/.local/share" ;;
esac
for FORTYTCHAT_DIAG_PYTHON in "${FORTYTCHAT_PYTHON:-}" \
    "$FORTYTCHAT_DIAG_DATA/fortytchat/venv/bin/python" \
    "$FORTYTCHAT_DIAG_DIR/.venv/bin/python" \
    "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$FORTYTCHAT_DIAG_PYTHON" ] && [ -x "$FORTYTCHAT_DIAG_PYTHON" ]; then
        exec "$FORTYTCHAT_DIAG_PYTHON" "$FORTYTCHAT_DIAG_DIR/tools/diagnose_session.py" "$@"
    fi
done
printf '%s\n' 'Python 3 est nécessaire. Aucun paquet ni droit administrateur n’est demandé.' >&2
exit 1
