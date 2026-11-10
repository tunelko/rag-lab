#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${RAG_SETUP_PYTHON:-}" ]]; then
    python_command="$RAG_SETUP_PYTHON"
elif command -v python3.13 >/dev/null 2>&1; then
    python_command=python3.13
else
    python_command=python3
fi
case "${1:-}" in
    --check|--start) wizard_mode="$1" ;;
    "") wizard_mode=--bootstrap ;;
    *) printf '%s\n' 'Uso: bash configurar.sh [--check|--start]' >&2; exit 2 ;;
esac
export PYTHONUTF8=1
exec "$python_command" "$project_root/configure_lab.py" "$wizard_mode"
