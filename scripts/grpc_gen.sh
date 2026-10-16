#!/usr/bin/env bash
# proto/ から Python のコード（メッセージ・スタブ・型ヒント）を生成する
set -euo pipefail
uv run python -m grpc_tools.protoc -I proto \
  --python_out=app/grpc_gen --grpc_python_out=app/grpc_gen --pyi_out=app/grpc_gen \
  proto/inventory/v1/inventory.proto
echo "generated: app/grpc_gen/inventory/v1/"
