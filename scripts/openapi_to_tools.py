"""1-2 OpenAPI 定義 → Function Calling（Tool Use）のツール定義へ変換する

    uv run python -m scripts.openapi_to_tools [--tag products] > tools.json

- summary / description がそのまま LLM の判断材料になる
- $ref を展開し、パスパラメータ・クエリパラメータ・リクエストボディを 1 つの input_schema にまとめる
"""

import argparse
import json
import re
from typing import Any

from scripts.export_openapi import export


def resolve(node: Any, spec: dict, depth: int = 0) -> Any:
    if depth > 10:
        return {}
    if isinstance(node, dict):
        if "$ref" in node:
            target: Any = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return resolve(target, spec, depth + 1)
        return {k: resolve(v, spec, depth) for k, v in node.items() if k not in ("title",)}
    if isinstance(node, list):
        return [resolve(v, spec, depth) for v in node]
    return node


def to_tools(spec: dict, tag: str | None) -> list[dict]:
    tools = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if tag and tag not in op.get("tags", []):
                continue
            if op.get("deprecated"):
                continue  # 非推奨 API はエージェントに使わせない
            props: dict[str, Any] = {}
            required: list[str] = []
            for prm in op.get("parameters", []):
                if prm["in"] not in ("path", "query"):
                    continue
                schema = resolve(prm.get("schema", {}), spec)
                if prm.get("description"):
                    schema["description"] = prm["description"]
                props[prm["name"]] = schema
                if prm.get("required"):
                    required.append(prm["name"])
            body = op.get("requestBody", {}).get("content", {})
            if body:
                props["body"] = resolve(next(iter(body.values()))["schema"], spec)
                required.append("body")
            name = re.sub(r"[^a-zA-Z0-9_-]", "_", op["operationId"])[:64]
            tools.append(
                {
                    "name": name,
                    "description": f"{op.get('summary', '')}。{op.get('description', '')} [{method.upper()} {path}]".strip(),
                    "input_schema": {"type": "object", "properties": props, "required": required},
                }
            )
    return tools


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default="products")
    args = p.parse_args()
    print(json.dumps(to_tools(export(), args.tag), ensure_ascii=False, indent=2))
