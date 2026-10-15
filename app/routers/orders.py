"""注文 API（PostgreSQL / Supabase）

- 2-1 リソース指向設計とステータスコード（201 + Location、409、コントローラーリソース）
- 2-2 バージョニング（/v1 → /v2）と Deprecation
- 2-3 カーソルベース / オフセットベースのページネーション、フィルタ、決定論的ソート
- 3-2 BOLA 対策（所有者 ID を必ず WHERE 句に結合）
- 4-2 Idempotency-Key と Transactional Outbox
"""

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.auth import AuthUser, current_user
from app.config import Settings, get_settings
from app.deprecation import deprecated_endpoint
from app.deps import get_mongo, get_pool
from app.idempotency import run_idempotent
from app.pagination import CursorPagination, OffsetPagination, decode_cursor, encode_cursor
from app.problems import ProblemException, problem_responses

router_v1 = APIRouter(prefix="/v1/orders", tags=["orders"])
router_v2 = APIRouter(prefix="/v2/orders", tags=["orders (v2)"])


class OrderStatus(StrEnum):
    pending = "pending"
    paid = "paid"
    shipped = "shipped"
    cancelled = "cancelled"


class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^SKU-\d{6}$", description="商品コード（例: SKU-000001）", examples=["SKU-000001"])
    quantity: int = Field(ge=1, le=99, description="数量（1〜99）")


class OrderCreate(BaseModel):
    """注文作成リクエスト。未定義のプロパティは拒否する（additionalProperties: false）"""

    model_config = ConfigDict(extra="forbid")
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=200)


class OrderItem(BaseModel):
    sku: str
    quantity: int
    unit_price: int


class OrderV1(BaseModel):
    id: UUID = Field(description="注文ID（UUID v7：時系列ソート可能）")
    order_no: int = Field(description="注文番号（画面表示用の連番）")
    status: OrderStatus
    items: list[OrderItem]
    total_yen: int = Field(description="合計金額（円）※v2 では Money オブジェクトに変更")
    note: str | None
    created_at: datetime
    updated_at: datetime


class Money(BaseModel):
    amount: int
    currency: Literal["JPY"] = "JPY"


class OrderV2(BaseModel):
    id: UUID
    order_no: int
    status: OrderStatus
    items: list[OrderItem]
    total: Money
    note: str | None
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    data: list[OrderV1]
    pagination: CursorPagination | OffsetPagination


class Cancellation(BaseModel):
    order_id: UUID
    status: OrderStatus
    cancelled_at: datetime


def to_v1(row: asyncpg.Record) -> OrderV1:
    return OrderV1(**{k: row[k] for k in OrderV1.model_fields})


def to_v2(row: asyncpg.Record) -> OrderV2:
    d = {k: row[k] for k in OrderV2.model_fields if k != "total"}
    return OrderV2(**d, total=Money(amount=row["total_yen"]))


COLUMNS = "id, order_no, status, items, total_yen, note, created_at, updated_at"
NO_STORE = {"Cache-Control": "no-store"}  # 個人の注文情報は共有キャッシュさせない


# ---------------------------------------------------------------- 作成（2-1 / 4-2）
@router_v1.post(
    "",
    status_code=201,
    response_model=OrderV1,
    summary="注文を作成する",
    description=(
        "`Idempotency-Key` ヘッダーを付けると、同一キーの再送で注文が二重に作成されない。"
        "成功時は 201 Created と `Location` ヘッダーを返す。"
    ),
    responses=problem_responses(400, 401, 409, 422),
)
async def create_order(
    body: OrderCreate,
    request: Request,
    user: AuthUser = Depends(current_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=8, max_length=255),
    pool: asyncpg.Pool = Depends(get_pool),
    mongo: AsyncDatabase = Depends(get_mongo),
    s: Settings = Depends(get_settings),
):
    async def perform() -> tuple[int, Any, dict[str, str]]:
        skus = sorted({i.sku for i in body.items})
        prices = {
            p["sku"]: p["price"] async for p in mongo.products.find({"sku": {"$in": skus}}, {"sku": 1, "price": 1})
        }
        missing = [sku for sku in skus if sku not in prices]
        if missing:
            raise ProblemException(
                422,
                "unknown-sku",
                invalid_params=[{"name": "items.sku", "reason": f"{m} は存在しません"} for m in missing],
            )
        items = [{"sku": i.sku, "quantity": i.quantity, "unit_price": prices[i.sku]} for i in body.items]
        total = sum(i["quantity"] * i["unit_price"] for i in items)

        await asyncio.sleep(s.demo_order_delay_ms / 1000)  # 処理時間の再現（同時送信デモ用）

        # 業務データと outbox イベントを同一トランザクションでコミットする（Transactional Outbox）
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"""insert into orders (user_id, status, items, total_yen, note)
                    values ($1, 'pending', $2, $3, $4) returning {COLUMNS}""",
                user.id,
                items,
                total,
                body.note,
            )
            await conn.execute(
                """insert into outbox (aggregate_type, aggregate_id, event_type, payload)
                   values ('order', $1, 'order.created', $2)""",
                row["id"],
                {"order_id": str(row["id"]), "user_id": str(user.id), "items": items, "total_yen": total},
            )
        order = to_v1(row)
        return 201, order.model_dump(mode="json"), {"Location": f"/v1/orders/{order.id}"}

    status, resp, headers = await run_idempotent(
        pool, user.id, idempotency_key, request, body.model_dump(mode="json"), perform
    )
    return JSONResponse(resp, status_code=status, headers={**headers, **NO_STORE})


# ---------------------------------------------------------------- 一覧（2-3）
@router_v1.get(
    "",
    response_model=OrderListResponse,
    summary="自分の注文一覧（カーソル / オフセット）",
    description=(
        "既定はカーソルベース（`cursor` に前回の `next_cursor` を渡す）。"
        "`page` を指定するとオフセットベースになり `total_count` を返すが、深いページほど遅くなる（比較用）。"
    ),
    responses=problem_responses(400, 401, 422),
)
async def list_orders(
    response: Response,
    user: AuthUser = Depends(current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    status: OrderStatus | None = Query(None, description="ステータスで絞り込み"),
    created_at_gte: datetime | None = Query(None, description="作成日時の下限（以上）"),
    created_at_lte: datetime | None = Query(None, description="作成日時の上限（以下）"),
    sort: Literal["-created_at", "created_at"] = Query("-created_at", description="`-` は降順"),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="前回レスポンスの next_cursor"),
    page: int | None = Query(None, ge=1, description="オフセット方式（比較用）。cursor と同時指定不可"),
):
    response.headers.update(NO_STORE)
    desc = sort.startswith("-")
    where, args = ["user_id = $1"], [user.id]

    def arg(v: Any) -> str:
        args.append(v)
        return f"${len(args)}"

    if status:
        where.append(f"status = {arg(status.value)}")
    if created_at_gte:
        where.append(f"created_at >= {arg(created_at_gte)}")
    if created_at_lte:
        where.append(f"created_at <= {arg(created_at_lte)}")

    # 決定論的ソート：created_at が重複しても順序が揺れないよう id をタイブレーカーに付与
    order_by = "created_at desc, id desc" if desc else "created_at asc, id asc"

    if page is not None:
        if cursor:
            raise ProblemException(400, "malformed-request", "cursor と page は同時に指定できません")
        where_sql = " and ".join(where)
        rows = await pool.fetch(
            f"select {COLUMNS} from orders where {where_sql} order by {order_by} "
            f"limit {arg(limit)} offset {arg((page - 1) * limit)}",
            *args,
        )
        total = await pool.fetchval(f"select count(*) from orders where {where_sql}", *args[: len(args) - 2])
        return OrderListResponse(
            data=[to_v1(r) for r in rows],
            pagination=OffsetPagination(page=page, per_page=limit, total_count=total),
        )

    if cursor:
        c = decode_cursor(cursor)
        try:
            c_at, c_id = datetime.fromisoformat(c["created_at"]), UUID(c["id"])
        except (KeyError, ValueError) as e:
            raise ProblemException(400, "invalid-cursor") from e
        op = "<" if desc else ">"
        where.append(f"(created_at, id) {op} ({arg(c_at)}, {arg(c_id)})")

    rows = await pool.fetch(
        f"select {COLUMNS} from orders where {' and '.join(where)} order by {order_by} limit {arg(limit + 1)}",
        *args,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor({"created_at": rows[-1]["created_at"].isoformat(), "id": str(rows[-1]["id"])})
        if has_more
        else None
    )
    return OrderListResponse(
        data=[to_v1(r) for r in rows],
        pagination=CursorPagination(next_cursor=next_cursor, has_more=has_more, limit=limit),
    )


# ---------------------------------------------------------------- 取得（v1：非推奨）
async def _get_owned(pool: asyncpg.Pool, order_id: UUID, user: AuthUser) -> asyncpg.Record:
    # BOLA 対策：ID だけで照会せず、所有者 ID を必ず結合する。他人の注文は存在ごと隠蔽して 404
    row = await pool.fetchrow(f"select {COLUMNS} from orders where id = $1 and user_id = $2", order_id, user.id)
    if row is None:
        raise ProblemException(404, "not-found", "注文が見つかりません")
    return row


deprecate_v1_get = deprecated_endpoint(
    "v1-get-order",
    deprecated_at=datetime(2026, 7, 1, tzinfo=UTC),
    sunset_at=datetime(2027, 3, 31, tzinfo=UTC),
    successor="/v2/orders",
)


@router_v1.get(
    "/{order_id}",
    response_model=OrderV1,
    deprecated=True,
    summary="注文を取得する（非推奨：/v2/orders/{order_id} へ移行）",
    responses=problem_responses(401, 404, 503),
)
async def get_order_v1(
    order_id: UUID,
    response: Response,
    user: AuthUser = Depends(current_user),
    _: None = Depends(deprecate_v1_get),
    pool: asyncpg.Pool = Depends(get_pool),
):
    response.headers.update(NO_STORE)
    return to_v1(await _get_owned(pool, order_id, user))


@router_v2.get(
    "/{order_id}",
    response_model=OrderV2,
    summary="注文を取得する（v2：金額を Money オブジェクトで返す）",
    responses=problem_responses(401, 404),
)
async def get_order_v2(
    order_id: UUID,
    response: Response,
    user: AuthUser = Depends(current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    response.headers.update(NO_STORE)
    return to_v2(await _get_owned(pool, order_id, user))


# ---------------------------------------------------------------- キャンセル（2-1 コントローラーリソース）
@router_v1.post(
    "/{order_id}/cancellation",
    status_code=201,
    response_model=Cancellation,
    summary="注文をキャンセルする（キャンセルリソースの作成）",
    description="動詞（/cancel）ではなく「キャンセル」というリソースを作成する形でモデル化する。",
    responses=problem_responses(401, 404, 409),
)
async def cancel_order(
    order_id: UUID,
    response: Response,
    user: AuthUser = Depends(current_user),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """update orders set status = 'cancelled', updated_at = now()
                where id = $1 and user_id = $2 and status in ('pending', 'paid')
            returning id, status, updated_at""",
            order_id,
            user.id,
        )
        if row is None:
            current = await conn.fetchval("select status from orders where id = $1 and user_id = $2", order_id, user.id)
            if current is None:
                raise ProblemException(404, "not-found", "注文が見つかりません")
            raise ProblemException(409, "order-not-cancellable", f"現在のステータスは {current} です")
        await conn.execute(
            """insert into outbox (aggregate_type, aggregate_id, event_type, payload)
               values ('order', $1, 'order.cancelled', $2)""",
            order_id,
            {"order_id": str(order_id), "user_id": str(user.id)},
        )
    response.headers["Location"] = f"/v2/orders/{order_id}"
    response.headers.update(NO_STORE)
    return Cancellation(order_id=row["id"], status=row["status"], cancelled_at=row["updated_at"])
