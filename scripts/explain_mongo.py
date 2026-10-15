"""2-3 ESR ルールの効果確認（MongoDB）

    uv run python -m scripts.explain_mongo

同じクエリ（category 等値 + price 範囲 + created_at 降順ソート）を、
ESR 順のインデックスと ERS 順のインデックスで実行し、走査量とメモリソートの有無を比べる。
"""

import asyncio
import json

from app.config import get_settings
from app.db import create_mongo

QUERY = {"category": "books", "price": {"$gte": 1000, "$lte": 5000}}
SORT = [("created_at", -1), ("_id", -1)]


def has_stage(plan: dict, stage: str) -> bool:
    return f'"stage": "{stage}"' in json.dumps(plan)


async def main() -> None:
    s = get_settings()
    client = create_mongo(s)
    coll = client[s.mongodb_db].products
    print(f"query={QUERY} sort=created_at desc limit=20\n")
    try:
        for hint in ["esr_category_created_price", "ers_category_price_created"]:
            ex = await coll.find(QUERY).sort(SORT).limit(20).hint(hint).explain()
            st = ex["executionStats"]
            sort_in_memory = has_stage(ex["queryPlanner"]["winningPlan"], "SORT")
            print(f"=== {hint}")
            print(f"  返却件数           {st['nReturned']}")
            print(f"  インデックス走査   {st['totalKeysExamined']:,}")
            print(f"  ドキュメント走査   {st['totalDocsExamined']:,}")
            print(f"  メモリソート       {'あり（SORT ステージ）' if sort_in_memory else 'なし'}")
            print(f"  実行時間           {st['executionTimeMillis']} ms\n")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
