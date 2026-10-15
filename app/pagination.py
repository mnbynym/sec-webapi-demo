"""2-3 カーソルベースページネーションの共通部品"""

import base64
import json
from typing import Any

from pydantic import BaseModel

from app.problems import ProblemException


class CursorPagination(BaseModel):
    next_cursor: str | None
    has_more: bool
    limit: int


class OffsetPagination(BaseModel):
    page: int
    per_page: int
    total_count: int


def encode_cursor(data: dict[str, Any]) -> str:
    """内部実装（created_at と id）を隠蔽するため、JSON を Base64URL で包んで返す"""
    raw = json.dumps(data, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError as e:
        raise ProblemException(400, "invalid-cursor", "cursor をデコードできません") from e
