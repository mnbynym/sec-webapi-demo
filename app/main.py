import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import create_mongo, create_pg_pool, create_redis
from app.graphql_api import naive_router as graphql_naive_router
from app.graphql_api import router as graphql_router
from app.problems import PROBLEM_TYPES, ProblemException, install_problem_handlers
from app.routers import inventory, orders, products, realtime, users, vuln
from app.telemetry import setup_logging, setup_telemetry

log = logging.getLogger("app")
settings = get_settings()
BROWSER_DIR = Path(__file__).resolve().parent.parent / "clients" / "browser"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg = await create_pg_pool(settings)
    mongo_client = create_mongo(settings)
    app.state.mongo = mongo_client[settings.mongodb_db]
    app.state.redis = create_redis(settings)
    app.state.http = httpx.AsyncClient()
    log.info("started mode=%s", settings.app_mode)
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await mongo_client.close()
    await app.state.pg.close()


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="WebAPI 設計・実装デモ",
        version="1.0.0",
        summary="研修テキスト「WebAPIの設計と実装のコツ」のデモ API",
        description=(
            "FastAPI + PostgreSQL(Supabase) + MongoDB(Atlas) + Redis。"
            "エラーはすべて RFC 9457（application/problem+json）で返す。"
        ),
        lifespan=lifespan,
    )
    if settings.otel_enabled:
        setup_telemetry(app, settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Location", "ETag", "RateLimit", "RateLimit-Policy", "Retry-After", "X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = rid
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        log.info(
            "%s %s -> %s (%.0fms)",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    install_problem_handlers(app)

    app.include_router(realtime.router)
    app.include_router(orders.router_v1)
    app.include_router(orders.router_v2)
    app.include_router(products.router)
    app.include_router(users.router)
    app.include_router(users.admin)
    app.include_router(inventory.router)
    app.include_router(inventory.downstream)
    app.include_router(graphql_router)  # 1-1 GraphQL（DataLoader あり）← 追加
    app.include_router(graphql_naive_router)  # 1-1 GraphQL（N+1 比較用）← 追加
    if settings.enable_vuln_endpoints:
        app.include_router(vuln.router)

    app.mount("/demo", StaticFiles(directory=BROWSER_DIR, html=True), name="demo")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request):
        checks = {}
        for name, probe in {
            "postgres": lambda: request.app.state.pg.fetchval("select 1"),
            "mongodb": lambda: request.app.state.mongo.command("ping"),
            "redis": lambda: request.app.state.redis.ping(),
        }.items():
            try:
                await probe()
                checks[name] = "ok"
            except Exception as e:  # noqa: BLE001
                checks[name] = f"error: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/problems/{slug}", include_in_schema=False, response_class=HTMLResponse)
    async def problem_doc(slug: str):
        # Problem Details の type は、人間向けドキュメントとして参照できる URI にしておく
        if slug not in PROBLEM_TYPES:
            raise ProblemException(404, "not-found")
        title, desc = PROBLEM_TYPES[slug]
        return f"<!doctype html><meta charset=utf-8><title>{title}</title><h1>{title}</h1><p>{desc}</p>"

    @app.get("/docs/migration-v2", include_in_schema=False, response_class=HTMLResponse)
    async def migration_guide():
        return (
            "<!doctype html><meta charset=utf-8><title>v2 移行ガイド</title>"
            "<h1>GET /v1/orders/{id} → GET /v2/orders/{id}</h1>"
            "<p><code>total_yen</code>（整数）は <code>total</code>（{amount, currency}）に変わります。</p>"
        )

    def custom_openapi():
        # FastAPI が自動付与する 422（HTTPValidationError）を RFC 9457 の ProblemDetails に差し替える
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            servers=[{"url": settings.api_base_url, "description": "demo"}],
        )
        problem = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}
        for ops in schema.get("paths", {}).values():
            for op in ops.values():
                res = op.get("responses", {}).get("422")
                if res and "application/json" in res.get("content", {}):
                    op["responses"]["422"] = {"description": "Validation Error", "content": problem}
        comps = schema.setdefault("components", {}).setdefault("schemas", {})
        for name in ("HTTPValidationError", "ValidationError"):
            comps.pop(name, None)
        if "ProblemDetails" not in comps:
            from app.problems import InvalidParam, ProblemDetails

            comps["ProblemDetails"] = ProblemDetails.model_json_schema(ref_template="#/components/schemas/{model}")
            comps.pop("InvalidParam", None)
            comps["InvalidParam"] = InvalidParam.model_json_schema()
            comps["ProblemDetails"].pop("$defs", None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app = create_app()
