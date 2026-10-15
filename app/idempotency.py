"""4-2 Idempotency-Key による冪等性の担保（IETF draft-ietf-httpapi-idempotency-key-header 準拠）

状態遷移: (なし) -> started -> finished
- 同一キー・同一内容で完了済み      -> 保存済みレスポンスを再生（Idempotent-Replayed: true）
- 同一キーで処理中                  -> 409 Conflict
- 同一キーで内容が異なる（改ざん）  -> 422 Unprocessable Content
- 処理失敗                          -> キーを解放して再試行可能にする
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Request

from app.problems import ProblemException

Result = tuple[int, Any, dict[str, str]]
TTL = "24 hours"


def request_fingerprint(request: Request, body: Any) -> str:
    payload = json.dumps(
        {"method": request.method, "path": request.url.path, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def run_idempotent(
    pool: asyncpg.Pool,
    user_id: UUID,
    key: str | None,
    request: Request,
    body: Any,
    perform: Callable[[], Awaitable[Result]],
) -> Result:
    if key is None:
        return await perform()

    fingerprint = request_fingerprint(request, body)
    async with pool.acquire() as conn:
        # TTL 切れのキーは存在しないものとして扱う
        await conn.execute(
            f"delete from idempotency_keys where user_id=$1 and key=$2 and created_at < now() - interval '{TTL}'",
            user_id,
            key,
        )
        inserted = await conn.fetchval(
            """insert into idempotency_keys (user_id, key, method, path, request_hash, status)
               values ($1, $2, $3, $4, $5, 'started')
               on conflict do nothing returning true""",
            user_id,
            key,
            request.method,
            request.url.path,
            fingerprint,
        )
        if not inserted:
            row = await conn.fetchrow("select * from idempotency_keys where user_id=$1 and key=$2", user_id, key)
            if row is None:
                raise ProblemException(409, "idempotency-key-in-progress", headers={"Retry-After": "1"})
            if row["request_hash"] != fingerprint:
                raise ProblemException(
                    422, "idempotency-key-reused", "同じ Idempotency-Key で異なる内容が送信されました"
                )
            if row["status"] == "started":
                raise ProblemException(409, "idempotency-key-in-progress", headers={"Retry-After": "1"})
            headers = {**(row["response_headers"] or {}), "Idempotent-Replayed": "true"}
            return row["response_status"], row["response_body"], headers

    try:
        status, resp_body, headers = await perform()
    except BaseException:
        # 失敗時はキーを解放してクライアントの再試行を許可する
        # （4xx の結果もキャッシュするかは要件次第。ここでは解放する方針）
        await pool.execute("delete from idempotency_keys where user_id=$1 and key=$2", user_id, key)
        raise

    # 本番では業務処理と同一トランザクションで finished に更新するのが望ましい
    await pool.execute(
        """update idempotency_keys
              set status='finished', response_status=$3, response_body=$4, response_headers=$5
            where user_id=$1 and key=$2""",
        user_id,
        key,
        status,
        resp_body,
        headers,
    )
    return status, resp_body, headers
