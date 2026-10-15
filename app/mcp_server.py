"""1-2 MCP サーバー：既存の WebAPI を AI エージェント向けのツールとして公開する

- Claude Code などの MCP クライアントから stdio で起動される（.mcp.json 参照）
- description が LLM の判断基準になるため「何をするか・いつ使うか・引数の例」を明記する
- LLM のコンテキストを圧迫しないよう、レスポンスは必要なフィールドだけに絞る
- 参照系のみを公開し、更新系（注文作成など）は公開しない（Human-in-the-Loop の境界）

    uv run python -m app.mcp_server
"""

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import get_settings

mcp = FastMCP("webapi-demo")
BASE = get_settings().api_base_url


@mcp.tool()
async def search_products(
    category: str | None = None, max_price: int | None = None, keyword: str | None = None, limit: int = 5
) -> str:
    """商品カタログを検索する。ユーザーが商品を探している・予算内の商品を知りたいときに使う。
    在庫数は返さない（在庫は get_inventory を使う）。

    Args:
        category: books, electronics, food, toys, apparel, sports, home, beauty のいずれか
        max_price: 上限価格（円）。例: 3000
        keyword: 商品名の部分一致。例: "Book"
        limit: 最大件数（1〜20）
    """
    params = {"fields": "sku,name,price,category", "limit": max(1, min(limit, 20))}
    if category:
        params["category"] = category
    if max_price is not None:
        params["price_lte"] = max_price
    if keyword:
        params["q"] = keyword
    async with httpx.AsyncClient(base_url=BASE, timeout=5) as c:
        r = await c.get("/v1/products", params=params)
    if r.status_code != 200:
        # LLM が自己修正できるよう、エラー内容をそのまま返す
        return f"エラー {r.status_code}: {r.text}"
    rows = r.json()["data"]
    return "\n".join(f"{p['sku']} | {p['name']} | {p['category']} | {p['price']}円" for p in rows) or "該当なし"


@mcp.tool()
async def get_inventory(sku: str) -> str:
    """指定した商品の在庫数と倉庫を返す。SKU は SKU-000001 の形式。"""
    async with httpx.AsyncClient(base_url=BASE, timeout=5) as c:
        r = await c.get(f"/v1/inventory/{sku}", params={"fallback": "true"})
    if r.status_code != 200:
        return f"エラー {r.status_code}: {r.json().get('title', r.text)}"
    d = r.json()
    return f"{d['sku']}: 在庫 {d['available']} 個（{d['warehouse']}）" + ("※古い値" if d.get("stale") else "")


if __name__ == "__main__":
    mcp.run()
