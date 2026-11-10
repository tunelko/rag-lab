#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="$project_root/.venv/bin/python"
if [[ ! -x "$python_command" ]]; then
    printf '%s\n' 'Run bash configurar.sh first to install the environment.' >&2
    exit 1
fi
export PYTHONUTF8=1
exec "$python_command" "$project_root/server_control.py" "$@"
