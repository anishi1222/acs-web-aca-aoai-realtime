#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"

# Canonical entrypoint: start the unified gateway (public 8000 + /ws/media).
cd "$ROOT_DIR/server"


VENV_PY="./.venv/bin/python"

# Prefer uv to guarantee Python 3.12+ (project requires-python >=3.12).
if command -v uv >/dev/null 2>&1; then
	# Create venv if missing, explicitly targeting Python 3.12.
	if [ ! -x "$VENV_PY" ]; then
		echo "Creating venv with Python 3.12 via uv..." >&2
		uv venv --python 3.12
	fi

	# Use lockfile for reproducibility.
	uv sync --frozen
else
	# If uv is not installed, require an existing venv.
	if [ ! -x "$VENV_PY" ]; then
		echo "ERROR: uv is not installed and venv is missing (expected ./server/.venv)." >&2
		echo "HINT: Install uv (recommended) then run: (cd server && uv venv --python 3.12 && uv sync --frozen)" >&2
		echo "HINT: Or run via Docker (python:3.12-slim) instead of local Python 3.9." >&2
		exit 1
	fi

	"$VENV_PY" -m pip install -r requirements.txt
fi

# 環境変数は server/.env から読み込み（秘密情報をスクリプトに直書きしない）
if [ -f .env ]; then
	set -a
	# shellcheck disable=SC1091
	source .env
	set +a
fi

exec "$VENV_PY" -u app.py
