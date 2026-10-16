"""protoc が生成したコード（scripts/grpc_gen.sh で再生成する。手で編集しない）

生成コードは `from inventory.v1 import ...` で自分自身を参照するため、このディレクトリを import パスに加える。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inventory.v1 import inventory_pb2, inventory_pb2_grpc  # noqa: E402

__all__ = ["inventory_pb2", "inventory_pb2_grpc"]
