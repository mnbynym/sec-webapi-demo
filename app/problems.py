"""2-4 統一エラーレスポンス（RFC 9457 Problem Details）"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.problems")

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_BASE = "/problems/"

# type ごとに title は固定（同一 type なら毎回同じ文字列）
PROBLEM_TYPES: dict[str, tuple[str, str]] = {
    "malformed-request": ("リクエストの構文が不正です", "JSON の構文エラーなど、サーバーが解釈できないリクエスト。"),
    "validation-failed": ("入力内容に不備があります", "構文は正しいが、スキーマ制約を満たさないリクエスト。"),
    "unauthorized": ("認証が必要です", "Bearer トークンがない、または無効。"),
    "forbidden": ("この操作を行う権限がありません", "認証済みだが、機能レベルの権限が不足している。"),
    "not-found": ("リソースが見つかりません", "存在しない、またはアクセス権のないリソース。"),
    "conflict": ("リソースの状態と競合しています", "重複登録や状態遷移の不整合。"),
    "order-not-cancellable": ("この注文はキャンセルできません", "発送済み・キャンセル済みの注文はキャンセル不可。"),
    "unknown-sku": ("存在しない商品が指定されました", "注文明細に存在しない SKU が含まれている。"),
    "idempotency-key-in-progress": ("同じ Idempotency-Key のリクエストを処理中です", "しばらく待って再試行する。"),
    "idempotency-key-reused": ("Idempotency-Key が別の内容で再利用されました", "キーは同一内容の再送にのみ使う。"),
    "precondition-failed": ("リソースが更新されています", "If-Match の ETag が現在の版と一致しない。"),
    "invalid-cursor": ("カーソルが不正です", "next_cursor の値をそのまま渡す。"),
    "rate-limited": ("リクエストが多すぎます", "Retry-After 秒待ってから再試行する。"),
    "brownout": ("計画停止中です（Brownout）", "廃止予定の API の計画的な一時停止。移行先を利用する。"),
    "upstream-unavailable": ("依存サービスが利用できません", "下流サービスへの接続に失敗した。"),
    "upstream-timeout": ("依存サービスがタイムアウトしました", "デッドライン内に下流サービスが応答しなかった。"),
    "internal-error": ("サーバー内部でエラーが発生しました", "詳細は request_id でサーバーログを参照する。"),
}


class InvalidParam(BaseModel):
    name: str
    reason: str


class ProblemDetails(BaseModel):
    """RFC 9457 Problem Details"""

    model_config = ConfigDict(extra="allow")
    type: str = Field(examples=["/problems/validation-failed"])
    status: int
    title: str
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    invalid_params: list[InvalidParam] | None = None


def problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI に 4xx/5xx の ProblemDetails を宣言するためのヘルパー"""
    return {c: {"model": ProblemDetails, "content": {PROBLEM_MEDIA_TYPE: {}}} for c in codes}


class ProblemException(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        self.status = status
        self.slug = slug
        self.detail = detail
        self.headers = headers or {}
        self.extensions = extensions


def _build(
    request: Request,
    status: int,
    slug: str | None,
    detail: str | None,
    headers: dict[str, str] | None = None,
    **ext: Any,
) -> JSONResponse:
    if slug and slug in PROBLEM_TYPES:
        type_, title = f"{PROBLEM_BASE}{slug}", PROBLEM_TYPES[slug][0]
    else:
        type_, title = "about:blank", HTTPStatus(status).phrase
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {
        "type": type_,
        "status": status,
        "title": title,
        "detail": detail,
        "instance": request.url.path,
        "request_id": request_id,
    }
    span_ctx = trace.get_current_span().get_span_context()
    if span_ctx.is_valid:
        body["trace_id"] = format(span_ctx.trace_id, "032x")
    body.update({k: v for k, v in ext.items() if v is not None})
    body = {k: v for k, v in body.items() if v is not None}
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_MEDIA_TYPE)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def _problem(request: Request, exc: ProblemException):
        return _build(request, exc.status, exc.slug, exc.detail, exc.headers, **exc.extensions)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        # JSON の構文エラーは 400、スキーマ違反は 422 に分ける（2-1）
        if any(e.get("type") == "json_invalid" for e in errors):
            return _build(request, 400, "malformed-request", "リクエストボディが JSON として解釈できません")
        params = [{"name": ".".join(str(p) for p in e["loc"]), "reason": e["msg"]} for e in errors]
        return _build(
            request,
            422,
            "validation-failed",
            "指定されたパラメータの値が制約を満たしていません",
            invalid_params=params,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        slug = {401: "unauthorized", 403: "forbidden", 404: "not-found", 409: "conflict"}.get(exc.status_code)
        detail = exc.detail if isinstance(exc.detail, str) else None
        return _build(request, exc.status_code, slug, detail, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # スタックトレースや SQL 例外はレスポンスに含めず、ログにだけ残す
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", None))
        return _build(request, 500, "internal-error", None)
