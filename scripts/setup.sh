#!/usr/bin/env bash
# Dev Container の postCreateCommand。依存関係のインストールまでを行う（Codespaces prebuild でキャッシュされる）
set -euo pipefail
command -v uv >/dev/null || pip install --user --quiet uv
export PATH="$HOME/.local/bin:$PATH"
uv sync
npm ci --no-audit --no-fund
[ -f .env ] || cp .env.example .env
echo "setup done. 次は make bootstrap を実行してください"
