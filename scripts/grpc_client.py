"""1-1 gRPC クライアント（生成されたスタブで呼び出す）

uv run python -m scripts.grpc_client            # すべて順に実行（make grpc-demo）
uv run python -m scripts.grpc_client unary      # unary / errors / deadline / size / stream のいずれか
"""

import asyncio
import gzip
import json
import sys
import time

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.text_format import MessageToString

from app.grpc_gen import inventory_pb2 as pb
from app.grpc_gen import inventory_pb2_grpc as pb_grpc

TARGET = "localhost:50051"


def heading(text: str) -> None:
    print(f"\n=== {text}")


async def unary(stub: pb_grpc.InventoryServiceStub) -> None:
    heading("Unary RPC：生成されたスタブを型付きのメソッドとして呼ぶ")
    started = time.perf_counter()
    stock = await stub.GetStock(pb.GetStockRequest(sku="SKU-000001"), timeout=2)
    print(MessageToString(stock, as_utf8=True).rstrip())
    print(f"warehouse は enum: {pb.Warehouse.Name(stock.warehouse)} / {(time.perf_counter() - started) * 1000:.1f} ms")


async def errors(stub: pb_grpc.InventoryServiceStub) -> None:
    heading("エラー：HTTP ステータスではなく gRPC のステータスコードで返る")
    for sku in ["ABC", "SKU-999999"]:
        try:
            await stub.GetStock(pb.GetStockRequest(sku=sku), timeout=2)
        except grpc.aio.AioRpcError as e:
            print(f"{sku:<12} → {e.code().name:<17} {e.details()}")


async def deadline(stub: pb_grpc.InventoryServiceStub) -> None:
    heading("デッドライン：クライアントが 0.5 秒を指定、サーバー処理は 2 秒かかる")
    started = time.perf_counter()
    try:
        await stub.GetStock(pb.GetStockRequest(sku="SKU-000001"), timeout=0.5, metadata=[("x-demo-delay-ms", "2000")])
    except grpc.aio.AioRpcError as e:
        print(f"{e.code().name} after {(time.perf_counter() - started) * 1000:.0f} ms")
        print("サーバーは残り時間（grpc-timeout ヘッダーで伝播）を見て、処理を始めずに中断している")


async def size(stub: pb_grpc.InventoryServiceStub) -> None:
    heading("ペイロードサイズ：同じ 100 件の在庫を Protobuf と JSON で比較")
    resp = await stub.BatchGetStock(pb.BatchGetStockRequest(skus=[f"SKU-{i:06d}" for i in range(1, 101)]), timeout=5)
    proto_bytes = resp.SerializeToString()
    json_bytes = json.dumps(MessageToDict(resp), separators=(",", ":")).encode()
    print(f"{'':10}{'raw':>10}{'gzip':>10}")
    print(f"{'Protobuf':10}{len(proto_bytes):>10,}{len(gzip.compress(proto_bytes)):>10,}")
    print(f"{'JSON':10}{len(json_bytes):>10,}{len(gzip.compress(json_bytes)):>10,}")
    print("Protobuf はフィールド名を送らず番号と型だけで表現する。圧縮後は差が縮まる点にも注意")


async def stream(stub: pb_grpc.InventoryServiceStub) -> None:
    heading("Server streaming RPC：1 回の呼び出しでサーバーから複数のメッセージを受け取る")
    async for ev in stub.WatchStock(pb.WatchStockRequest(sku="SKU-000001", max_events=5), timeout=10):
        print(f"{time.strftime('%H:%M:%S')} available={ev.stock.available:<4} delta={ev.delta:+d}")


STEPS = {"unary": unary, "errors": errors, "deadline": deadline, "size": size, "stream": stream}


async def main() -> None:
    names = sys.argv[1:] or list(STEPS)
    async with grpc.aio.insecure_channel(TARGET) as channel:
        try:
            await asyncio.wait_for(channel.channel_ready(), timeout=3)
        except TimeoutError:
            sys.exit(f"{TARGET} に接続できません。make grpc-server を先に起動してください")
        stub = pb_grpc.InventoryServiceStub(channel)
        for name in names:
            await STEPS[name](stub)


if __name__ == "__main__":
    asyncio.run(main())
