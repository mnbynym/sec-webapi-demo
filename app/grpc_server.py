"""1-1 gRPC サーバー（在庫サービス）

    uv run python -m app.grpc_server          # :50051 で起動（make grpc-server）

- 契約は proto/inventory/v1/inventory.proto。コードは scripts/grpc_gen.sh で生成する
- Server Reflection を有効にしているので、grpcurl は .proto なしでサービス一覧・型を取得できる
  （本番では情報公開を避けるため無効にすることが多い）
- エラーは HTTP ステータスではなく gRPC のステータスコード（INVALID_ARGUMENT、NOT_FOUND 等）で返す
- クライアントが指定したデッドラインの残り時間は context.time_remaining() で取得できる
"""

import asyncio
import hashlib
import logging
import random
import re
import time

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from grpc_reflection.v1alpha import reflection

from app.grpc_gen import inventory_pb2 as pb
from app.grpc_gen import inventory_pb2_grpc as pb_grpc

log = logging.getLogger("grpc")
PORT = 50051
SKU_RE = re.compile(r"^SKU-\d{6}$")
WAREHOUSES = [pb.WAREHOUSE_TOKYO, pb.WAREHOUSE_OSAKA, pb.WAREHOUSE_FUKUOKA]
DEFAULT_EVENTS, MAX_EVENTS = 5, 20


def _stock(sku: str, available: int | None = None) -> pb.Stock:
    h = int(hashlib.sha256(sku.encode()).hexdigest(), 16)
    ts = Timestamp()
    ts.GetCurrentTime()
    return pb.Stock(
        sku=sku,
        available=h % 120 if available is None else available,
        warehouse=WAREHOUSES[h % 3],
        updated_at=ts,
    )


async def _validate(sku: str, context: grpc.aio.ServicerContext) -> None:
    if not SKU_RE.match(sku):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"sku の形式が不正です: {sku!r}")
    if int(sku[4:]) >= 900_000:  # デモ用：SKU-9xxxxx は存在しない扱い
        await context.abort(grpc.StatusCode.NOT_FOUND, f"{sku} は存在しません")


async def _simulate_work(context: grpc.aio.ServicerContext) -> None:
    """メタデータ x-demo-delay-ms で処理時間を再現する（デッドライン超過のデモ用）"""
    md = dict(context.invocation_metadata() or [])
    delay = int(md.get("x-demo-delay-ms", "0") or 0)
    remaining = context.time_remaining()
    if remaining is not None and delay / 1000 > remaining:
        # 残り時間内に終わらない処理は始めない（デッドライン伝播と同じ考え方）
        log.info("skip work: delay=%dms > remaining=%.0fms", delay, remaining * 1000)
        await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "デッドラインまでに処理を完了できません")
    await asyncio.sleep(delay / 1000)


class InventoryService(pb_grpc.InventoryServiceServicer):
    async def GetStock(self, request: pb.GetStockRequest, context: grpc.aio.ServicerContext) -> pb.Stock:
        started = time.perf_counter()
        await _validate(request.sku, context)
        await _simulate_work(context)
        log.info("GetStock %s (%.1fms)", request.sku, (time.perf_counter() - started) * 1000)
        return _stock(request.sku)

    async def BatchGetStock(
        self, request: pb.BatchGetStockRequest, context: grpc.aio.ServicerContext
    ) -> pb.BatchGetStockResponse:
        if len(request.skus) > 1000:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "skus は 1000 件までです")
        for sku in request.skus:
            await _validate(sku, context)
        log.info("BatchGetStock %d skus", len(request.skus))
        return pb.BatchGetStockResponse(stocks=[_stock(s) for s in request.skus])

    async def WatchStock(self, request: pb.WatchStockRequest, context: grpc.aio.ServicerContext):
        await _validate(request.sku, context)
        n = min(request.max_events or DEFAULT_EVENTS, MAX_EVENTS)
        available = _stock(request.sku).available
        log.info("WatchStock %s start (%d events)", request.sku, n)
        for _ in range(n):
            await asyncio.sleep(0.5)
            delta = random.choice([-3, -2, -1, 1, 2])
            available = max(0, available + delta)
            yield pb.StockEvent(stock=_stock(request.sku, available), delta=delta)
        log.info("WatchStock %s done", request.sku)


async def serve(port: int = PORT) -> None:
    server = grpc.aio.server()
    pb_grpc.add_InventoryServiceServicer_to_server(InventoryService(), server)
    names = (pb.DESCRIPTOR.services_by_name["InventoryService"].full_name, reflection.SERVICE_NAME)
    reflection.enable_server_reflection(names, server)
    server.add_insecure_port(f"0.0.0.0:{port}")  # デモのため平文。本番は TLS（add_secure_port）にする
    await server.start()
    log.info("gRPC InventoryService listening on :%d (reflection enabled)", port)
    await server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(serve())
