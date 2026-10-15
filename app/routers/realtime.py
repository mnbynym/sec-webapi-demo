"""1-1 REST / SSE / WebSocket の使い分け

同じ「生成AI風の文章生成」を 3 方式で返し、体感の違いを比較する。
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/v1/demo", tags=["1-1 realtime"])

ANSWER = (
    "WebAPI の方式選定では、外部公開には REST、画面ごとに必要なデータが大きく異なる場合は GraphQL、"
    "サービス間通信には gRPC を選ぶ。サーバーから逐次データを押し出すなら、単方向は SSE、"
    "双方向は WebSocket が基本になる。"
)
TOKEN_INTERVAL = 0.06


def tokens() -> list[str]:
    # 疑似トークン化（2〜3文字ずつ）
    return [ANSWER[i : i + 3] for i in range(0, len(ANSWER), 3)]


class Completion(BaseModel):
    prompt: str
    text: str
    tokens: int


@router.get("/rest/completion", response_model=Completion, summary="REST：生成完了後に一括で返す")
async def rest_completion(prompt: str = Query("APIの方式選定のコツは？", max_length=200)):
    toks = tokens()
    await asyncio.sleep(TOKEN_INTERVAL * len(toks))  # 生成時間の再現
    return Completion(prompt=prompt, text="".join(toks), tokens=len(toks))


@router.get(
    "/sse/completion",
    summary="SSE：トークン単位でストリーミング",
    description=(
        "Content-Type: text/event-stream。各イベントに id を付与しているため、切断後に EventSource が"
        "Last-Event-ID ヘッダー付きで自動再接続すると、続きから再開する。"
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def sse_completion(
    prompt: str = Query("APIの方式選定のコツは？", max_length=200),
    last_event_id: int | None = Header(None, alias="Last-Event-ID"),
):
    toks = tokens()
    start = (last_event_id + 1) if last_event_id is not None else 0

    async def stream() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"  # 再接続間隔（ミリ秒）
        for i in range(start, len(toks)):
            await asyncio.sleep(TOKEN_INTERVAL)
            yield f"id: {i}\nevent: token\ndata: {json.dumps({'text': toks[i]}, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps({'tokens': len(toks)})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ChatRoom:
    """インメモリの接続管理。複数インスタンスにスケールする場合は Redis Pub/Sub などが必要"""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)


room = ChatRoom()


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, name: str = Query("guest", max_length=20)):
    await ws.accept()
    room.clients.add(ws)
    await room.broadcast({"system": True, "text": f"{name} が参加しました（{len(room.clients)}人）"})
    try:
        while True:
            text = (await ws.receive_text())[:500]
            await room.broadcast({"from": name, "text": text})
    except WebSocketDisconnect:
        room.clients.discard(ws)
        await room.broadcast({"system": True, "text": f"{name} が退出しました"})
