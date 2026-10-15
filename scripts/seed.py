"""デモデータの投入（既に投入済みならスキップする）

    uv run python -m scripts.seed            # 既定件数（SEED_ORDERS / SEED_PRODUCTS）
    uv run python -m scripts.seed --small    # 動作確認用の少量データ
    uv run python -m scripts.seed --force    # 大量データを作り直す

- Supabase Auth: alice / bob / carol（carol は管理者）
- PostgreSQL   : profiles、alice の大量注文（2-3 ページネーション比較用）、bob の注文 3 件
- MongoDB      : 商品カタログ
"""

import argparse
import asyncio
import time
from uuid import UUID

import httpx

from app.config import Settings, get_settings
from app.db import create_mongo, create_pg_pool
from scripts.catalog import BOB_ORDERS, DEMO_USERS, price_of, product

ORDER_BATCH = 100_000
PRODUCT_BATCH = 5_000


def admin_headers(s: Settings) -> dict[str, str]:
    key = s.supabase_service_role_key
    if not key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY が未設定です（.env を確認）")
    h = {"apikey": key}
    if not key.startswith("sb_"):  # 新形式の secret key は JWT ではないため Bearer に載せない
        h["Authorization"] = f"Bearer {key}"
    return h


async def ensure_auth_user(http: httpx.AsyncClient, s: Settings, email: str) -> UUID:
    base = f"{s.supabase_url.rstrip('/')}/auth/v1/admin/users"
    r = await http.post(
        base, headers=admin_headers(s), json={"email": email, "password": s.demo_password, "email_confirm": True}
    )
    if r.status_code in (200, 201):
        return UUID(r.json()["id"])
    # 既に存在する場合は一覧から探す
    r2 = await http.get(base, headers=admin_headers(s), params={"page": 1, "per_page": 1000})
    r2.raise_for_status()
    for u in r2.json().get("users", []):
        if u.get("email") == email:
            # パスワードを既定値に揃える
            await http.put(f"{base}/{u['id']}", headers=admin_headers(s), json={"password": s.demo_password})
            return UUID(u["id"])
    raise SystemExit(f"Supabase Auth にユーザーを作成できません: {email} {r.status_code} {r.text}")


async def seed_users(pool, s: Settings) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    async with httpx.AsyncClient(timeout=10) as http:
        for email, name, role in DEMO_USERS:
            uid = await ensure_auth_user(http, s, email)
            await pool.execute("delete from profiles where email = $1 and id <> $2", email, uid)
            await pool.execute(
                """insert into profiles (id, email, display_name, role) values ($1, $2, $3, $4)
                   on conflict (id) do update set email = excluded.email,
                       display_name = excluded.display_name, role = excluded.role""",
                uid,
                email,
                name,
                role,
            )
            ids[email] = uid
            print(f"user   : {email:<20} {uid} ({role})")
    return ids


async def seed_orders(pool, alice: UUID, bob: UUID, target: int, force: bool) -> None:
    current = await pool.fetchval("select count(*) from orders where user_id = $1 and source = 'seed'", alice)
    if current >= target and not force:
        print(f"orders : alice {current:,} 件（スキップ）")
    else:
        await pool.execute("delete from orders where user_id = $1 and source = 'seed'", alice)
        started = time.perf_counter()
        for start in range(1, target + 1, ORDER_BATCH):
            end = min(start + ORDER_BATCH - 1, target)
            await pool.execute(
                """insert into orders (id, user_id, status, items, total_yen, source, created_at, updated_at)
                   select api_demo.uuid_generate_v7(ts), $1,
                          (array['pending','paid','shipped','cancelled'])[1 + (g % 4)],
                          jsonb_build_array(jsonb_build_object(
                              'sku', 'SKU-' || lpad(((g % 1000) + 1)::text, 6, '0'),
                              'quantity', 1 + g % 3, 'unit_price', 1000 + (g % 50) * 100)),
                          (1 + g % 3) * (1000 + (g % 50) * 100), 'seed', ts, ts
                     from (select g, now() - make_interval(secs => g * 30) as ts
                             from generate_series($2::int, $3::int) g) s""",
                alice,
                start,
                end,
            )
            print(f"orders : alice {end:,}/{target:,}", end="\r", flush=True)
        await pool.execute("analyze orders")
        print(f"\norders : alice {target:,} 件（{time.perf_counter() - started:.0f}s）")

    await pool.execute("delete from orders where user_id = $1 and source = 'seed'", bob)
    for note, status, lines in BOB_ORDERS:
        items = [{"sku": sku, "quantity": q, "unit_price": price_of(int(sku[4:]))} for sku, q in lines]
        await pool.execute(
            """insert into orders (user_id, status, items, total_yen, note, source)
               values ($1, $2, $3, $4, $5, 'seed')""",
            bob,
            status,
            items,
            sum(i["quantity"] * i["unit_price"] for i in items),
            note,
        )
    print(f"orders : bob {len(BOB_ORDERS)} 件")


async def seed_products(s: Settings, target: int, force: bool) -> None:
    client = create_mongo(s)
    coll = client[s.mongodb_db].products
    try:
        current = await coll.count_documents({"source": "seed"})
        if current >= target and not force:
            print(f"products: {current:,} 件（スキップ）")
            return
        await coll.delete_many({"source": "seed"})
        for start in range(1, target + 1, PRODUCT_BATCH):
            end = min(start + PRODUCT_BATCH - 1, target)
            await coll.insert_many([product(i) for i in range(start, end + 1)], ordered=False)
            print(f"products: {end:,}/{target:,}", end="\r", flush=True)
        print(f"\nproducts: {target:,} 件")
    finally:
        await client.close()


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--small", action="store_true", help="注文 1 万件・商品 5 千件")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    s = get_settings()
    n_orders, n_products = (10_000, 5_000) if args.small else (s.seed_orders, s.seed_products)
    pool = await create_pg_pool(s, max_size=2)
    try:
        ids = await seed_users(pool, s)
        await seed_products(s, n_products, args.force)
        await seed_orders(pool, ids["alice@example.com"], ids["bob@example.com"], n_orders, args.force)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
