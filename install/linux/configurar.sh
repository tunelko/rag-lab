#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${RAG_SETUP_PYTHON:-}" ]]; then
    python_command="$RAG_SETUP_PYTHON"
elif command -v python3.13 >/dev/null 2>&1; then
    python_command=python3.13
else
    python_command=python3
fi
# Use the same language as the wizard even when Python is missing.
language="${RAG_SETUP_LANGUAGE:-$(head -n 1 "$project_root/.cache/setup-language" 2>/dev/null || true)}"
language="${language:-${LC_ALL:-${LANG:-}}}"
case "${1:-}" in
    --check|--start) wizard_mode="$1" ;;
    "") wizard_mode=--bootstrap ;;
    *)
        case "$language" in
            es*) printf '%s\n' 'Uso: bash install/linux/configurar.sh [--check|--start]' >&2 ;;
            *) printf '%s\n' 'Usage: bash install/linux/configurar.sh [--check|--start]' >&2 ;;
        esac
        exit 2 ;;
esac
if ! command -v -- "$python_command" >/dev/null 2>&1; then
    case "$language" in
        es*) printf '%s\n' 'Instala CPython 3.13.x x86_64 con venv y ensurepip. Comprueba la versión con: python3.13 -V' >&2 ;;
        *) printf '%s\n' 'Install x86_64 CPython 3.13.x with venv and ensurepip. Check the version with: python3.13 -V' >&2 ;;
    esac
    exit 1
fi
export PYTHONUTF8=1
exec "$python_command" "$project_root/install/configure_lab.py" "$wizard_mode"
