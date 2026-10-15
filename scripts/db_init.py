"""PostgreSQL のスキーマと MongoDB のインデックスを作成する（何度実行しても安全）

uv run python -m scripts.db_init
"""

import asyncio
from pathlib import Path

import asyncpg
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.config import get_settings
from app.db import create_mongo

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def init_postgres() -> None:
    s = get_settings()
    conn = await asyncpg.connect(s.database_url, statement_cache_size=0)
    try:
        await conn.execute(SCHEMA.read_text())
        print("postgres: schema api_demo ready")
    finally:
        await conn.close()


async def init_mongo() -> None:
    s = get_settings()
    client = create_mongo(s)
    db = client[s.mongodb_db]
    try:
        await db.products.create_indexes(
            [
                IndexModel([("sku", ASCENDING)], unique=True, name="sku_unique"),
                # ESR ルール：Equality(category) → Sort(created_at, _id) → Range(price)
                IndexModel(
                    [("category", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING), ("price", ASCENDING)],
                    name="esr_category_created_price",
                ),
                # 比較用（ERS 順）：範囲条件を先に置くとソートにインデックスが使えずメモリソートになる
                IndexModel(
                    [("category", ASCENDING), ("price", ASCENDING), ("created_at", DESCENDING)],
                    name="ers_category_price_created",
                ),
                IndexModel([("created_at", DESCENDING), ("_id", DESCENDING)], name="created_desc"),
            ]
        )
        print("mongodb: indexes ready")
    finally:
        await client.close()


async def main() -> None:
    await init_postgres()
    await init_mongo()


if __name__ == "__main__":
    asyncio.run(main())
