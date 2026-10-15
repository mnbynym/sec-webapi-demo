#!/usr/bin/env bash
# 5-1 Schemathesis：OpenAPI 定義からテストケースを自動生成し、仕様どおりに応答するかを検証する
# 5xx の発生、スキーマ不一致のレスポンス、未定義のステータスコードなどを検出する
set -euo pipefail
TOKEN=$(uv run python -m scripts.token alice)
uvx 'schemathesis>=4' run http://localhost:8000/openapi.json \
  --header "Authorization: Bearer ${TOKEN}" \
  --exclude-path-regex '^/(vuln|v1/demo)' \
  --max-examples 25 \
  "$@"
echo "※ 注文が作成されるため、終了後に make reset を実行する"
