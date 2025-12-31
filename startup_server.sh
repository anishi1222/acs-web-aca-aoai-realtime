#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"

# Canonical entrypoint: start the unified gateway (public 8000 + /ws/media).
cd "$ROOT_DIR/server"

if command -v uv >/dev/null 2>&1; then
	# Use lockfile when available for reproducibility.
	if [ -f "./uv.lock" ]; then
		uv sync --frozen
	else
		uv sync
	fi
	# shellcheck disable=SC1091
	source ./.venv/bin/activate
else
	# venv は server/.venv に統一
	if [ ! -f "./.venv/bin/activate" ]; then
		echo "ERROR: venv not found. Expected ./server/.venv" >&2
		echo "HINT: Install uv (recommended) or create a venv manually, then install requirements.txt." >&2
		exit 1
	fi

	# shellcheck disable=SC1091
	source ./.venv/bin/activate

	python -m pip install -r requirements.txt
fi

# 環境変数は server/.env から読み込み（秘密情報をスクリプトに直書きしない）
if [ -f .env ]; then
	set -a
	# shellcheck disable=SC1091
	source .env
	set +a
fi

exec python -u app.py
