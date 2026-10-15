"""⚠ 意図的に脆弱なエンドポイント（3-2 / 3-4 の比較デモ専用）

ENABLE_VULN_ENDPOINTS=false で無効化される。本番コードに絶対に持ち込まないこと。
安全版との差分を見比べられるよう、対応する安全版をコメントに記す。
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends

from app.auth import AuthUser, current_user
from app.deps import get_pool
from app.problems import ProblemException
from app.routers.orders import COLUMNS, OrderV1, to_v1
from app.routers.users import Profile

router = APIRouter(prefix="/vuln", tags=["⚠ vulnerable (demo only)"])


@router.get("/orders/{order_no}", response_model=OrderV1, summary="BOLA：注文番号だけで照会（脆弱）")
async def vuln_get_order(order_no: int, _: AuthUser = Depends(current_user), pool: asyncpg.Pool = Depends(get_pool)):
    # ✖ 連番 + 所有者チェックなし → 他人の注文番号を指定すると閲覧できてしまう
    # ✔ 安全版: GET /v1/orders/{order_id}（UUID + "and user_id = $2"）
    row = await pool.fetchrow(f"select {COLUMNS} from orders where order_no = $1", order_no)
    if row is None:
        raise ProblemException(404, "not-found")
    return to_v1(row)


@router.get("/admin/users", response_model=list[Profile], summary="BFLA：認証だけで管理者 API を実行（脆弱）")
async def vuln_admin_users(_: AuthUser = Depends(current_user), pool: asyncpg.Pool = Depends(get_pool)):
    # ✖ 「ログインしていれば OK」になっており、ロール（機能レベル）の認可がない
    # ✔ 安全版: GET /v1/admin/users（Depends(require_admin)）
    rows = await pool.fetch("select id, email, display_name, role, created_at from profiles order by email")
    return [Profile(**dict(r)) for r in rows]


# ORM の「モデルの全カラムをリクエストからバインドする」実装を再現するためのカラム一覧
_PROFILE_COLUMNS = {"display_name", "email", "role"}


@router.patch("/users/me", response_model=Profile, summary="Mass Assignment：受け取った JSON をそのまま反映（脆弱）")
async def vuln_patch_me(
    body: dict[str, Any] = Body(..., examples=[{"display_name": "Alice", "role": "admin"}]),
    user: AuthUser = Depends(current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    # ✖ リクエストの全キーをモデルに流し込む → role を送れば管理者に昇格できる
    # ✔ 安全版: PATCH /v1/users/me（ProfileUpdate DTO + extra="forbid"）
    changes = {k: v for k, v in body.items() if k in _PROFILE_COLUMNS}
    if changes:
        sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(changes))
        await pool.execute(f"update profiles set {sets} where id = $1", user.id, *changes.values())
    row = await pool.fetchrow("select id, email, display_name, role, created_at from profiles where id=$1", user.id)
    return Profile(**dict(row))
