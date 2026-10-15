"""研修前の事前チェック。すべて ✔ になればデモを開始できる

uv run python -m scripts.doctor
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable

import httpx

from app.config import get_settings
from app.db import create_mongo, create_pg_pool, create_redis
from scripts.envfile import HTTP_ENV

s = get_settings()
results: list[tuple[bool, str, str]] = []


async def check(name: str, fn: Callable[[], Awaitable[str]], required: bool = True) -> None:
    try:
        detail = await asyncio.wait_for(fn(), timeout=10)
        results.append((True, name, detail))
    except Exception as e:  # noqa: BLE001
        results.append((not required, name, f"{'✖' if required else '△'} {type(e).__name__}: {e}"))


async def postgres() -> str:
    pool = await create_pg_pool(s, max_size=1)
    try:
        orders = await pool.fetchval("select count(*) from orders")
        users = await pool.fetchval("select count(*) from profiles")
        if users < 3:
            raise RuntimeError("profiles が不足（make seed）")
        return f"orders={orders:,} profiles={users}"
    finally:
        await pool.close()


async def mongodb() -> str:
    c = create_mongo(s)
    try:
        n = await c[s.mongodb_db].products.estimated_document_count()
        idx = await c[s.mongodb_db].products.index_information()
        if "esr_category_created_price" not in idx:
            raise RuntimeError("インデックス未作成（make db-init）")
        return f"products={n:,}"
    finally:
        await c.close()


async def redis() -> str:
    r = create_redis(s)
    try:
        await r.ping()
        return "ping ok"
    finally:
        await r.aclose()


async def get(url: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=5, verify=False) as http:
        return await http.get(url, **kw)


async def supabase_auth() -> str:
    r = await get(f"{s.supabase_url}/auth/v1/health", headers={"apikey": s.supabase_anon_key})
    r.raise_for_status()
    async with httpx.AsyncClient(timeout=5) as http:
        t = await http.post(
            f"{s.supabase_url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": s.supabase_anon_key},
            json={"email": "alice@example.com", "password": s.demo_password},
        )
        t.raise_for_status()
        token = t.json()["access_token"]
    w = await get(f"{s.api_base_url}/v1/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    w.raise_for_status()
    return f"login alice ok / alg={w.json()['alg']}"


async def api() -> str:
    r = await get(f"{s.api_base_url}/readyz")
    r.raise_for_status()
    return str(r.json()["checks"])


async def toxiproxy() -> str:
    r = await get("http://localhost:8474/proxies")
    r.raise_for_status()
    inv = await get(f"{s.api_base_url}/v1/inventory/SKU-000001")
    inv.raise_for_status()
    return f"proxies={list(r.json())} / inventory via proxy ok"


async def grafana() -> str:
    r = await get("http://localhost:3000/api/health")
    r.raise_for_status()
    return f"grafana ok / OTEL_ENABLED={s.otel_enabled}"


async def caddy() -> str:
    r = await get("https://localhost:8443/healthz")
    return f"alt-svc={r.headers.get('alt-svc', '-')}"


async def http_env() -> str:
    if not HTTP_ENV.exists():
        raise RuntimeError("clients/http/.env がない（make http-env）")
    return "ok"


async def main() -> None:
    print(f"mode={s.app_mode} api={s.api_base_url} supabase={s.supabase_url}\n")
    await check("PostgreSQL", postgres)
    await check("MongoDB", mongodb)
    await check("Redis", redis)
    await check("API (make run)", api)
    await check("Supabase Auth + JWT 検証", supabase_auth)
    await check("Toxiproxy (4-4)", toxiproxy)
    await check("Grafana otel-lgtm (5-3)", grafana, required=False)
    await check("Caddy HTTP/3 (4-1)", caddy, required=False)
    await check("REST Client .env", http_env)
    for ok, name, detail in results:
        mark = "✔" if ok and not detail.startswith("△") else ("△" if ok else "✖")
        print(f" {mark} {name:<28} {detail.lstrip('✖△ ')}")
    if not all(ok for ok, _, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
