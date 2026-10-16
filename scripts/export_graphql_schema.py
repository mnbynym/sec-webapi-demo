"""GraphQL のスキーマ（SDL）を contracts/schema.graphql に書き出す（DB 接続は不要）

uv run python -m scripts.export_graphql_schema
"""

from pathlib import Path

from app.graphql_api import schema

path = Path(__file__).resolve().parent.parent / "contracts" / "schema.graphql"
path.write_text(schema.as_str() + "\n")
print(f"wrote {path.relative_to(path.parents[1])}")
