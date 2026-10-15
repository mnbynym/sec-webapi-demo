"""商品 API（MongoDB / Atlas）

- 2-3 フィルタ・ソート・カーソルページネーション、Sparse Fieldsets、ESR インデックス
- 3-3 レートリミット（一覧は未認証でも呼べるため IP 単位で制限）
- 4-3 キャッシュ：Cache-Control / ETag / 304、Redis の Cache-Aside（TTL ジッター・単一化・ネガティブキャッシュ）
- 2-1 / 3-2 管理者のみ作成・更新・削除（409 / 412 / 204、BFLA 対策）
"""

import asyncio
import json
import random
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from app.auth import AuthUser, require_admin
from app.deps import get_mongo, get_redis
from app.pagination import CursorPagination, decode_cursor, encode_cursor
from app.problems import ProblemException, problem_responses
from app.ratelimit import rate_limited

router = APIRouter(prefix="/v1/products", tags=["products"])

CACHE_TTL = 300
NEGATIVE_TTL = 30
PUBLIC_CACHE = "public, max-age=60, stale-while-revalidate=300"


class Category(StrEnum):
    books = "books"
    electronics = "electronics"
    food = "food"
    toys = "toys"
    apparel = "apparel"
    sports = "sports"
    home = "home"
    beauty = "beauty"


class Product(BaseModel):
    sku: str
    name: str
    category: Category
    price: int
    tags: list[str]
    description: str
    created_at: datetime
    version: int = Field(description="楽観的ロック用のバージョン（ETag に使用）")


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^SKU-\d{6}$")
    name: str = Field(min_length=1, max_length=100)
    category: Category
    price: int = Field(ge=0, le=10_000_000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=2000)


class ProductPatch(BaseModel):
    """JSON Merge Patch（RFC 7396）。指定したフィールドだけ更新する"""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=100)
    category: Category | None = None
    price: int | None = Field(None, ge=0, le=10_000_000)
    tags: list[str] | None = Field(None, max_length=10)
    description: str | None = Field(None, max_length=2000)


class ProductListResponse(BaseModel):
    data: list[dict[str, Any]]
    pagination: CursorPagination


FIELDS = set(Product.model_fields)
PROJECTION_BASE = {"_id": 0, "source": 0}


def etag_of(doc: dict[str, Any]) -> str:
    return f'"{doc["sku"]}-v{doc["version"]}"'


def cache_key(sku: str) -> str:
    return f"cache:product:{sku}"


# ---------------------------------------------------------------- 一覧（2-3 / 3-3）
@router.get(
    "",
    response_model=ProductListResponse,
    summary="商品を検索する",
    description=(
        "フィルタ（category, price_gte, price_lte, q）、ソート（created_at 昇順/降順）、"
        "カーソルページネーション、`fields` による返却フィールドの絞り込みに対応。"
        "未認証でも呼べるため、接続元 IP 単位でレートリミットをかける。"
    ),
    responses=problem_responses(400, 422, 429),
    dependencies=[Depends(rate_limited("products-search"))],
)
async def list_products(
    response: Response,
    mongo: AsyncDatabase = Depends(get_mongo),
    category: Category | None = None,
    price_gte: int | None = Query(None, ge=0),
    price_lte: int | None = Query(None, ge=0),
    q: str | None = Query(None, min_length=1, max_length=50, description="商品名の部分一致"),
    fields: str | None = Query(None, description="返却フィールド（例: sku,name,price）"),
    sort: Literal["-created_at", "created_at"] = "-created_at",
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    flt: dict[str, Any] = {}
    if category:
        flt["category"] = category.value  # E: 等値条件
    if price_gte is not None or price_lte is not None:  # R: 範囲条件
        flt["price"] = {k: v for k, v in (("$gte", price_gte), ("$lte", price_lte)) if v is not None}
    if q:
        flt["name"] = {"$regex": re.escape(q)}  # ユーザー入力を正規表現としてそのまま使わない（ReDoS 対策）

    desc = sort.startswith("-")
    direction = -1 if desc else 1
    if cursor:
        c = decode_cursor(cursor)
        try:
            c_at, c_id = datetime.fromisoformat(c["created_at"]), ObjectId(c["id"])
        except (KeyError, ValueError, TypeError) as e:
            raise ProblemException(400, "invalid-cursor") from e
        op = "$lt" if desc else "$gt"
        flt["$or"] = [{"created_at": {op: c_at}}, {"created_at": c_at, "_id": {op: c_id}}]

    projection: dict[str, Any] = {"source": 0}
    if fields:
        requested = {f.strip() for f in fields.split(",") if f.strip()}
        unknown = requested - FIELDS
        if unknown:
            raise ProblemException(400, "malformed-request", f"未知のフィールド: {', '.join(sorted(unknown))}")
        projection = {f: 1 for f in requested | {"created_at"}}  # カーソル生成に created_at は必須

    docs = await (
        mongo.products.find(flt, projection)
        .sort([("created_at", direction), ("_id", direction)])  # S: ソート（_id をタイブレーカー）
        .limit(limit + 1)
        .to_list()
    )
    has_more = len(docs) > limit
    docs = docs[:limit]
    next_cursor = (
        encode_cursor({"created_at": docs[-1]["created_at"].isoformat(), "id": str(docs[-1]["_id"])})
        if has_more
        else None
    )
    data = []
    for d in docs:
        d.pop("_id", None)
        if fields and "created_at" not in requested:
            d.pop("created_at", None)
        data.append(d)
    response.headers["Cache-Control"] = "public, max-age=30"
    return ProductListResponse(
        data=data, pagination=CursorPagination(next_cursor=next_cursor, has_more=has_more, limit=limit)
    )


# ---------------------------------------------------------------- 取得（4-3）
async def load_product(mongo: AsyncDatabase, redis: Redis, sku: str) -> tuple[dict[str, Any] | None, str]:
    """Cache-Aside。戻り値は (商品 or None, HIT/MISS)"""
    key = cache_key(sku)
    cached = await redis.get(key)
    if cached is not None:
        return (json.loads(cached) or None), "HIT"

    # キャッシュスタンピード対策：最初の 1 リクエストだけが DB を読み、他は完成を待つ
    lock = await redis.set(f"lock:{key}", "1", nx=True, px=3000)
    if not lock:
        for _ in range(20):
            await asyncio.sleep(0.05)
            cached = await redis.get(key)
            if cached is not None:
                return (json.loads(cached) or None), "HIT"

    try:
        doc = await mongo.products.find_one({"sku": sku}, PROJECTION_BASE)
        if doc is None:
            # キャッシュペネトレーション対策：存在しない結果も短い TTL でキャッシュ
            await redis.set(key, "null", ex=NEGATIVE_TTL)
            return None, "MISS"
        payload = Product(**doc).model_dump(mode="json")
        ttl = int(CACHE_TTL * random.uniform(0.9, 1.1))  # TTL ジッターで一斉失効を防ぐ
        await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=ttl)
        return payload, "MISS"
    finally:
        if lock:
            await redis.delete(f"lock:{key}")


@router.get(
    "/{sku}",
    response_model=Product,
    summary="商品を取得する（ETag / 304 / Cache-Aside）",
    responses={304: {"description": "Not Modified"}, **problem_responses(404, 429)},
)
async def get_product(
    sku: str,
    response: Response,
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    mongo: AsyncDatabase = Depends(get_mongo),
    redis: Redis = Depends(get_redis),
):
    product, cache_status = await load_product(mongo, redis, sku)
    if product is None:
        raise ProblemException(404, "not-found", f"{sku} は存在しません", headers={"X-Cache": cache_status})
    etag = etag_of(product)
    headers = {"ETag": etag, "Cache-Control": PUBLIC_CACHE, "X-Cache": cache_status, "Vary": "Accept-Encoding"}
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return product


# ---------------------------------------------------------------- 管理者操作（2-1 / 3-2）
@router.post(
    "",
    status_code=201,
    response_model=Product,
    summary="商品を登録する（管理者）",
    responses=problem_responses(401, 403, 409, 422),
)
async def create_product(
    body: ProductCreate,
    response: Response,
    _: AuthUser = Depends(require_admin),
    mongo: AsyncDatabase = Depends(get_mongo),
    redis: Redis = Depends(get_redis),
):
    doc = {**body.model_dump(mode="json"), "created_at": datetime.now(UTC), "version": 1, "source": "api"}
    try:
        await mongo.products.insert_one(doc)
    except DuplicateKeyError as e:
        raise ProblemException(409, "conflict", f"{body.sku} は既に登録されています") from e
    await redis.delete(cache_key(body.sku))  # ネガティブキャッシュを消す
    response.headers["Location"] = f"/v1/products/{body.sku}"
    response.headers["ETag"] = etag_of(doc)
    return Product(**doc)


@router.patch(
    "/{sku}",
    response_model=Product,
    summary="商品を部分更新する（管理者、JSON Merge Patch + If-Match）",
    description="`If-Match` に取得時の ETag を指定すると、他者の更新と競合した場合に 412 を返す（楽観的ロック）。",
    responses=problem_responses(401, 403, 404, 412, 422),
)
async def patch_product(
    sku: str,
    response: Response,
    body: ProductPatch = Body(media_type="application/merge-patch+json"),
    if_match: str | None = Header(None, alias="If-Match"),
    _: AuthUser = Depends(require_admin),
    mongo: AsyncDatabase = Depends(get_mongo),
    redis: Redis = Depends(get_redis),
):
    changes = body.model_dump(mode="json", exclude_unset=True)
    nulls = [k for k, v in changes.items() if v is None]
    if nulls:
        raise ProblemException(
            422,
            "validation-failed",
            invalid_params=[{"name": k, "reason": "このフィールドは削除できません"} for k in nulls],
        )
    flt: dict[str, Any] = {"sku": sku}
    if if_match:
        m = re.fullmatch(r'"(?P<sku>SKU-\d{6})-v(?P<v>\d+)"', if_match.strip())
        if not m or m["sku"] != sku:
            raise ProblemException(412, "precondition-failed", "If-Match の値が不正です")
        flt["version"] = int(m["v"])

    doc = await mongo.products.find_one_and_update(
        flt,
        {"$set": changes, "$inc": {"version": 1}},
        projection=PROJECTION_BASE,
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        if await mongo.products.count_documents({"sku": sku}, limit=1):
            raise ProblemException(412, "precondition-failed", "他のリクエストで更新されています。再取得してください")
        raise ProblemException(404, "not-found", f"{sku} は存在しません")
    await redis.delete(cache_key(sku))  # キャッシュ無効化
    response.headers["ETag"] = etag_of(doc)
    return Product(**doc)


@router.delete(
    "/{sku}",
    status_code=204,
    summary="商品を削除する（管理者）",
    responses=problem_responses(401, 403, 404),
)
async def delete_product(
    sku: str,
    _: AuthUser = Depends(require_admin),
    mongo: AsyncDatabase = Depends(get_mongo),
    redis: Redis = Depends(get_redis),
):
    result = await mongo.products.delete_one({"sku": sku})
    if result.deleted_count == 0:
        raise ProblemException(404, "not-found", f"{sku} は存在しません")
    await redis.delete(cache_key(sku))
    return Response(status_code=204)
