"""1-1 GraphQL（Strawberry）：REST との比較用

同じ注文・商品データを GraphQL で公開し、次の論点を確認する。
- 必要なフィールドだけを 1 リクエストで取得する（オーバーフェッチ / アンダーフェッチの解消）
- N+1 問題と DataLoader（/graphql/naive と /graphql で外部クエリ回数を比較）
- クエリの深さ・エイリアス数・トークン数の制限（GraphQL 特有の DoS 対策）
- エラーは HTTP 200 + errors 配列で返る（REST のステータスコード設計との違い）

レスポンスの extensions.queryStats に、そのリクエストで発行した DB クエリ数を載せる。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import strawberry
from fastapi import Request
from graphql import GraphQLError
from strawberry.dataloader import DataLoader
from strawberry.extensions import (
    MaskErrors,
    MaxAliasesLimiter,
    MaxTokensLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)
from strawberry.fastapi import BaseContext, GraphQLRouter

from app.auth import AuthUser, verify_token
from app.config import get_settings
from app.pagination import decode_cursor, encode_cursor
from app.problems import ProblemException

PRODUCT_FIELDS = {"_id": 0, "sku": 1, "name": 1, "category": 1, "price": 1, "tags": 1}


# ---------------------------------------------------------------- コンテキスト
class GQLContext(BaseContext):
    """リクエストごとに作られる。DataLoader もリクエスト単位で持つ（キャッシュが他人と混ざらない）"""

    def __init__(self, request: Request, use_loader: bool) -> None:
        super().__init__()
        self.req = request
        self.pool = request.app.state.pg
        self.mongo = request.app.state.mongo
        self.use_loader = use_loader
        self.stats = {"mongoQueries": 0, "postgresQueries": 0}
        self.product_loader: DataLoader[str, dict[str, Any] | None] = DataLoader(load_fn=self._batch_products)
        self._user: AuthUser | None = None

    async def current_user(self) -> AuthUser:
        if self._user:
            return self._user
        header = self.req.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise GraphQLError("認証が必要です", extensions={"code": "UNAUTHENTICATED"})
        try:
            self._user = await verify_token(header[7:], get_settings())
        except ProblemException as e:
            raise GraphQLError(e.detail or "トークンを検証できません", extensions={"code": "UNAUTHENTICATED"}) from e
        return self._user

    async def product(self, sku: str) -> dict[str, Any] | None:
        if self.use_loader:
            return await self.product_loader.load(sku)
        # ✖ ナイーブ実装：明細 1 行ごとに 1 クエリ（N+1）
        self.stats["mongoQueries"] += 1
        return await self.mongo.products.find_one({"sku": sku}, PRODUCT_FIELDS)

    async def _batch_products(self, skus: list[str]) -> list[dict[str, Any] | None]:
        # ✔ DataLoader：同じイベントループの周回で集まった SKU を 1 回の $in クエリにまとめる（重複も除去される）
        self.stats["mongoQueries"] += 1
        docs = {d["sku"]: d async for d in self.mongo.products.find({"sku": {"$in": list(set(skus))}}, PRODUCT_FIELDS)}
        return [docs.get(s) for s in skus]


def _context_getter(use_loader: bool):
    async def get_context(request: Request) -> GQLContext:
        return GQLContext(request, use_loader)

    return get_context


# ---------------------------------------------------------------- 型
@strawberry.type(description="商品（MongoDB）")
class Product:
    sku: str
    name: str
    category: str
    price: int = strawberry.field(description="価格（円）")
    tags: list[str]

    @staticmethod
    def from_doc(d: dict[str, Any]) -> "Product":
        return Product(sku=d["sku"], name=d["name"], category=d["category"], price=d["price"], tags=d.get("tags", []))

    @strawberry.field(description="同じカテゴリの商品。ネストを深くできるため、深さ制限のデモに使う")
    async def related(self, info: strawberry.Info[GQLContext], first: int = 3) -> list["Product"]:
        info.context.stats["mongoQueries"] += 1
        cursor = info.context.mongo.products.find(
            {"category": self.category, "sku": {"$ne": self.sku}}, PRODUCT_FIELDS
        ).limit(min(first, 10))
        return [Product.from_doc(d) async for d in cursor]


@strawberry.type(description="注文明細")
class OrderItem:
    sku: str
    quantity: int
    unit_price: int

    @strawberry.field(description="明細の商品。DataLoader の有無で N+1 の発生が変わる")
    async def product(self, info: strawberry.Info[GQLContext]) -> Product | None:
        doc = await info.context.product(self.sku)
        return Product.from_doc(doc) if doc else None


@strawberry.type(description="注文（PostgreSQL）")
class Order:
    id: strawberry.ID
    order_no: int
    status: str
    total_yen: int
    created_at: datetime
    items: list[OrderItem]


@strawberry.type(description="カーソルベースのページ（Relay の Connection を簡略化した形）")
class OrderPage:
    nodes: list[Order]
    end_cursor: str | None
    has_next_page: bool


# ---------------------------------------------------------------- クエリ
@strawberry.type
class Query:
    @strawberry.field(description="商品を検索する（認証不要）")
    async def products(
        self, info: strawberry.Info[GQLContext], category: str | None = None, first: int = 10
    ) -> list[Product]:
        info.context.stats["mongoQueries"] += 1
        flt = {"category": category} if category else {}
        cursor = info.context.mongo.products.find(flt, PRODUCT_FIELDS).sort("created_at", -1).limit(min(first, 50))
        return [Product.from_doc(d) async for d in cursor]

    @strawberry.field(description="商品を 1 件取得する（認証不要）")
    async def product(self, info: strawberry.Info[GQLContext], sku: str) -> Product | None:
        doc = await info.context.product(sku)
        return Product.from_doc(doc) if doc else None

    @strawberry.field(description="自分の注文一覧（要認証）。所有者 ID で必ず絞り込む（BOLA 対策は REST と同じ）")
    async def my_orders(
        self, info: strawberry.Info[GQLContext], first: int = 10, after: str | None = None
    ) -> OrderPage:
        user = await info.context.current_user()
        first = max(1, min(first, 50))
        args: list[Any] = [user.id, first + 1]
        where = "user_id = $1"
        if after:
            try:
                c = decode_cursor(after)
                args += [datetime.fromisoformat(c["created_at"]), UUID(c["id"])]
            except (ProblemException, KeyError, ValueError) as e:
                raise GraphQLError("after が不正です", extensions={"code": "BAD_USER_INPUT"}) from e
            where += " and (created_at, id) < ($3, $4)"
        info.context.stats["postgresQueries"] += 1
        rows = await info.context.pool.fetch(
            f"select id, order_no, status, total_yen, created_at, items from orders where {where} "
            "order by created_at desc, id desc limit $2",
            *args,
        )
        has_next = len(rows) > first
        rows = rows[:first]
        nodes = [
            Order(
                id=strawberry.ID(str(r["id"])),
                order_no=r["order_no"],
                status=r["status"],
                total_yen=r["total_yen"],
                created_at=r["created_at"],
                items=[OrderItem(sku=i["sku"], quantity=i["quantity"], unit_price=i["unit_price"]) for i in r["items"]],
            )
            for r in rows
        ]
        end = (
            encode_cursor({"created_at": rows[-1]["created_at"].isoformat(), "id": str(rows[-1]["id"])})
            if has_next
            else None
        )
        return OrderPage(nodes=nodes, end_cursor=end, has_next_page=has_next)


# ---------------------------------------------------------------- 拡張
class QueryStats(SchemaExtension):
    """そのリクエストで発行した DB クエリ数をレスポンスの extensions に載せる（N+1 の可視化）"""

    def get_results(self) -> dict[str, Any]:
        ctx = self.execution_context.context
        stats = getattr(ctx, "stats", None)
        if stats is None:
            return {}
        return {"queryStats": {**stats, "dataLoader": ctx.use_loader}}


def _should_mask(error: GraphQLError) -> bool:
    # 意図して返す GraphQLError と検証エラーはそのまま、想定外の例外（DB エラー等）は内容を隠す
    return error.original_error is not None and not isinstance(error.original_error, GraphQLError)


schema = strawberry.Schema(
    query=Query,
    extensions=[
        QueryDepthLimiter(max_depth=5),  # 深いネストによる計算量の爆発を防ぐ
        MaxAliasesLimiter(max_alias_count=10),  # エイリアスで同じフィールドを大量に実行させる攻撃を防ぐ
        MaxTokensLimiter(max_token_count=1000),  # 巨大なクエリ文字列を拒否する
        MaskErrors(should_mask_error=_should_mask, error_message="サーバー内部でエラーが発生しました"),
        QueryStats,
    ],
)

# /graphql：DataLoader あり（推奨） / /graphql/naive：DataLoader なし（N+1 の比較用）
router = GraphQLRouter(schema, path="/graphql", context_getter=_context_getter(True), include_in_schema=False)
naive_router = GraphQLRouter(
    schema, path="/graphql/naive", context_getter=_context_getter(False), include_in_schema=False
)
