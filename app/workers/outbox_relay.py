"""4-2 Transactional Outbox のリレーと、受信側の冪等コンシューマ

outbox（PostgreSQL）→ リレー（このプロセス）→ コンシューマ（MongoDB に集計を反映）
本来はリレーとコンシューマの間に Kafka などのブローカーが入る。ここでは直接呼び出して簡略化している。

    uv run python -m app.workers.outbox_relay            # 常駐
    uv run python -m app.workers.outbox_relay --once     # 1 回だけ処理
    uv run python -m app.workers.outbox_relay --redeliver 5  # 配信済みイベントを重複配信（冪等性の確認）
"""

import argparse
import asyncio
import logging
from typing import Any

import asyncpg
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.db import create_mongo, create_pg_pool

log = logging.getLogger("outbox")
BATCH = 100


async def consume(mongo: AsyncDatabase, event: dict[str, Any]) -> bool:
    """冪等コンシューマ。イベント ID を一意キーとして記録し、処理済みならスキップする"""
    try:
        await mongo.order_events.insert_one(
            {"_id": event["id"], "type": event["event_type"], "order_id": event["aggregate_id"]}
        )
    except DuplicateKeyError:
        log.info("skip duplicate event %s", event["id"])
        return False

    payload = event["payload"]
    if event["event_type"] == "order.created":
        for item in payload["items"]:
            await mongo.sales_by_sku.update_one(
                {"_id": item["sku"]}, {"$inc": {"quantity": item["quantity"], "orders": 1}}, upsert=True
            )
    log.info("consumed %s %s", event["event_type"], event["id"])
    return True


async def relay_once(pool: asyncpg.Pool, mongo: AsyncDatabase) -> int:
    async with pool.acquire() as conn, conn.transaction():
        # SKIP LOCKED により、リレーを複数起動しても同じイベントを取り合わない
        rows = await conn.fetch(
            """select id::text, aggregate_id::text, event_type, payload from outbox
                where published_at is null order by created_at limit $1 for update skip locked""",
            BATCH,
        )
        for row in rows:
            await consume(mongo, dict(row))  # 配信 → 成功後に published_at を更新（At-least-once）
            await conn.execute("update outbox set published_at = now() where id = $1::uuid", row["id"])
    return len(rows)


async def redeliver(pool: asyncpg.Pool, mongo: AsyncDatabase, n: int) -> None:
    rows = await pool.fetch(
        """select id::text, aggregate_id::text, event_type, payload from outbox
            where published_at is not null order by created_at desc limit $1""",
        n,
    )
    for row in rows:
        await consume(mongo, dict(row))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--redeliver", type=int, default=0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    pool = await create_pg_pool(s, max_size=2)
    client = create_mongo(s)
    mongo = client[s.mongodb_db]
    try:
        if args.redeliver:
            await redeliver(pool, mongo, args.redeliver)
            return
        while True:
            n = await relay_once(pool, mongo)
            if args.once:
                log.info("relayed %d events", n)
                return
            if n == 0:
                await asyncio.sleep(args.interval)
    finally:
        await client.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
