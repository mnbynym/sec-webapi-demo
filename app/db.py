import json

import asyncpg
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from app.config import Settings


async def _init_conn(conn: asyncpg.Connection) -> None:
    # jsonb を Python の dict/list として扱う
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pg_pool(s: Settings, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        s.database_url,
        min_size=min_size,
        max_size=max_size,
        init=_init_conn,
        statement_cache_size=s.db_statement_cache_size,
        server_settings={"search_path": f"{s.db_schema},public", "application_name": "webapi-demo"},
    )


def create_mongo(s: Settings) -> AsyncMongoClient:
    return AsyncMongoClient(s.mongodb_uri, appname="webapi-demo", tz_aware=True, serverSelectionTimeoutMS=5000)


def create_redis(s: Settings) -> Redis:
    return Redis.from_url(s.redis_url, decode_responses=True)
