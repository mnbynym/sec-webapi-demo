"""FastAPI から OpenAPI 3.1 定義を書き出す（DB 接続は不要）

uv run python -m scripts.export_openapi                     # contracts/openapi.json
uv run python -m scripts.export_openapi --baseline          # contracts/openapi.baseline.json を更新
uv run python -m scripts.export_openapi --breaking-example  # 破壊的変更を加えた比較用定義を生成
"""

import argparse
import copy
import json
import os
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def export() -> dict:
    os.environ.setdefault("ENABLE_VULN_ENDPOINTS", "false")  # 脆弱版は契約に含めない
    from app.main import app

    return app.openapi()


def breaking_example(spec: dict) -> dict:
    """2-2 で挙げた破壊的変更の典型例を意図的に加える"""
    s = copy.deepcopy(spec)
    schemas = s["components"]["schemas"]
    # 1. レスポンスのフィールド削除：OrderV1.total_yen
    schemas["OrderV1"]["properties"].pop("total_yen")
    schemas["OrderV1"]["required"].remove("total_yen")
    # 2. 必須リクエストパラメータの追加：OrderCreate.note を必須化
    schemas["OrderCreate"].setdefault("required", []).append("note")
    # 3. データ型の変更：Product.price を integer → string
    schemas["Product"]["properties"]["price"] = {"type": "string", "title": "Price"}
    return s


def write(name: str, spec: dict) -> None:
    path = CONTRACTS / name
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {path.relative_to(CONTRACTS.parent)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--breaking-example", action="store_true")
    args = p.parse_args()
    spec = export()
    write("openapi.baseline.json" if args.baseline else "openapi.json", spec)
    if args.breaking_example:
        write("openapi.breaking-example.json", breaking_example(spec))
