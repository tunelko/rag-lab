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
    *)
        # Same order as the wizard: an explicit variable, the remembered choice, then the session's language.
        language="${RAG_SETUP_LANGUAGE:-$(head -n 1 "$project_root/.cache/setup-language" 2>/dev/null || true)}"
        case "${language:-${LC_ALL:-${LANG:-}}}" in
            es*) printf '%s\n' 'Uso: bash configurar.sh [--check|--start]' >&2 ;;
            *) printf '%s\n' 'Usage: bash configurar.sh [--check|--start]' >&2 ;;
        esac
        exit 2 ;;
esac
export PYTHONUTF8=1
exec "$python_command" "$project_root/configure_lab.py" "$wizard_mode"
