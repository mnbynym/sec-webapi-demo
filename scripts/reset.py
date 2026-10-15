"""デモで変化した状態を初期状態に戻す（大量のシードデータは残す）

uv run python -m scripts.reset
"""

import asyncio

import httpx

from app.config import get_settings
from app.db import create_mongo, create_pg_pool, create_redis
from scripts.catalog import BOB_ORDERS, DEMO_USERS, product

RESTORE_PRODUCTS = 100  # デモで更新・削除しうる先頭の商品を元に戻す


async def main() -> None:
    s = get_settings()

    pool = await create_pg_pool(s, max_size=1)
    try:
        n = await pool.fetchval(
            "with d as (delete from orders where source = 'api' returning 1) select count(*) from d"
        )
        for note, status, _ in BOB_ORDERS:
            await pool.execute("update orders set status = $2, updated_at = now() where note = $1", note, status)
        await pool.execute("truncate idempotency_keys, outbox")
        for email, name, role in DEMO_USERS:
            await pool.execute("update profiles set display_name = $2, role = $3 where email = $1", email, name, role)
        print(f"postgres: api 由来の注文 {n} 件を削除、冪等キー・outbox・プロフィールを初期化")
    finally:
        await pool.close()

    client = create_mongo(s)
    db = client[s.mongodb_db]
    try:
        await db.products.delete_many({"source": "api"})
        for i in range(1, RESTORE_PRODUCTS + 1):
            doc = product(i)
            await db.products.replace_one({"sku": doc["sku"]}, doc, upsert=True)
        await db.order_events.drop()
        await db.sales_by_sku.drop()
        print("mongodb : 商品を復元、イベント・集計を削除")
    finally:
        await client.close()

    redis = create_redis(s)
    await redis.flushdb()
    await redis.aclose()
    print("redis   : flushdb（キャッシュ・レートリミット・brownout・利用ログ）")

    try:
        async with httpx.AsyncClient(timeout=2) as http:
            await http.post("http://localhost:8474/reset")
        print("toxiproxy: 障害注入を解除")
    except httpx.HTTPError:
        print("toxiproxy: 未起動（スキップ）")


if __name__ == "__main__":
    asyncio.run(main())
