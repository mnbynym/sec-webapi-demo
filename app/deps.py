"""app.state に保持した接続を取り出す依存関数"""

import asyncpg
import httpx
from fastapi import Request
from pymongo.asynchronous.database import AsyncDatabase
from redis.asyncio import Redis


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg


def get_mongo(request: Request) -> AsyncDatabase:
    return request.app.state.mongo


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http
