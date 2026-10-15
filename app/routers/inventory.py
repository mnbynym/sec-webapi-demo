"""4-4 タイムアウト・デッドライン伝播・フォールバック

構成: クライアント → API(/v1/inventory) → Toxiproxy(:18000) → 下流サービス(/_downstream/inventory)
Toxiproxy で遅延・切断を注入し、API とクライアントの振る舞いを観察する。
下流サービスはデモを単純にするため同じプロセス内に同居させている（本来は別サービス）。
"""

import asyncio
import hashlib
import json
import time

import httpx
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.deps import get_http, get_redis
from app.problems import ProblemException, problem_responses

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])
downstream = APIRouter(prefix="/_downstream", include_in_schema=False)

DEFAULT_BUDGET_MS = 2000  # クライアントがデッドラインを指定しない場合の全体予算
CONNECT_TIMEOUT = 0.3  # 接続タイムアウトは短く
MAX_READ_TIMEOUT = 1.5  # 呼び出し元（クライアント）より短く設定する


class Inventory(BaseModel):
    sku: str
    available: int
    warehouse: str
    stale: bool = False


def remaining_ms(deadline: int | None, started: float) -> int:
    if deadline is not None:
        return deadline - int(time.time() * 1000)
    return DEFAULT_BUDGET_MS - int((time.monotonic() - started) * 1000)


@router.get(
    "/{sku}",
    response_model=Inventory,
    summary="在庫を取得する（下流サービス呼び出し）",
    description=(
        "`X-Request-Deadline`（UNIX 時刻ミリ秒）を受け取り、残り時間を下流へ伝播する。"
        "`fallback=true` なら下流障害時に最後に取得できた値（stale）を返す（機能縮退）。"
    ),
    responses=problem_responses(503, 504),
)
async def get_inventory(
    sku: str,
    response: Response,
    fallback: bool = Query(False, description="下流障害時に古い値で縮退応答する"),
    deadline: int | None = Header(None, alias="X-Request-Deadline"),
    http: httpx.AsyncClient = Depends(get_http),
    redis: Redis = Depends(get_redis),
    s: Settings = Depends(get_settings),
):
    started = time.monotonic()
    budget = remaining_ms(deadline, started)
    if budget <= 0:
        raise ProblemException(504, "upstream-timeout", "デッドラインを過ぎているため処理を中断しました")

    read_timeout = min(budget / 1000, MAX_READ_TIMEOUT)
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=read_timeout, write=1.0, pool=1.0)
    downstream_deadline = int(time.time() * 1000) + int(read_timeout * 1000)

    try:
        r = await http.get(
            f"{s.inventory_upstream_url}/_downstream/inventory/{sku}",
            headers={"X-Request-Deadline": str(downstream_deadline)},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        await redis.set(f"inventory:last:{sku}", json.dumps(data), ex=600)
        response.headers["Server-Timing"] = f"downstream;dur={(time.monotonic() - started) * 1000:.0f}"
        return Inventory(**data)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
        if fallback and (last := await redis.get(f"inventory:last:{sku}")):
            response.headers["Cache-Status"] = "webapi-demo; fwd=stale; detail=upstream-failure"
            return Inventory(**json.loads(last), stale=True)
        if isinstance(e, httpx.TimeoutException):
            raise ProblemException(504, "upstream-timeout", f"{read_timeout:.2f} 秒以内に応答がありませんでした") from e
        raise ProblemException(
            503, "upstream-unavailable", "在庫サービスに接続できません", headers={"Retry-After": "1"}
        ) from e


@downstream.get("/inventory/{sku}")
async def downstream_inventory(
    sku: str,
    deadline: int | None = Header(None, alias="X-Request-Deadline"),
    work_ms: int = Query(50, ge=0, le=10_000),
):
    # 受け取ったデッドラインを超えるなら処理しない（無駄なリソース消費を防ぐ）
    if deadline is not None and deadline - int(time.time() * 1000) < work_ms:
        raise ProblemException(504, "upstream-timeout", "デッドラインまでに処理を完了できません")
    await asyncio.sleep(work_ms / 1000)
    h = int(hashlib.sha256(sku.encode()).hexdigest(), 16)
    return {"sku": sku, "available": h % 120, "warehouse": ["tokyo", "osaka", "fukuoka"][h % 3]}
