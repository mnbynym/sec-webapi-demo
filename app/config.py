from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数（.env）から読み込む設定。local / cloud の切り替えは接続先の値だけで行う。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_mode: str = "local"  # local | cloud（表示用。挙動は接続先の値で決まる）
    api_base_url: str = "http://localhost:8000"

    # PostgreSQL（Supabase）
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    db_schema: str = "api_demo"  # public を避け、Supabase の Data API に露出させない
    # Supabase の Transaction pooler（6543番）経由では prepared statement が使えないため 0 にする
    db_statement_cache_size: int = 100

    # MongoDB（Atlas）
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "webapi_demo"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Supabase Auth
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""  # HS256（レガシー共有鍵）で署名されたトークンの検証用
    jwt_audience: str = "authenticated"
    jwt_allowed_algs: list[str] = ["ES256", "RS256", "HS256"]

    # 4-4 下流サービス（Toxiproxy 経由）
    inventory_upstream_url: str = "http://localhost:18000"

    # 3-3 レートリミット（トークンバケット）
    rate_limit_capacity: int = 10
    rate_limit_refill_per_sec: float = 2.0

    # 5-3 OpenTelemetry
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "webapi-demo"

    # デモ用
    demo_password: str = "Passw0rd!demo"
    demo_order_delay_ms: int = 300  # 4-2 同時リクエストで「処理中」を見せるための遅延
    enable_vuln_endpoints: bool = True  # 3-2 / 3-4 の脆弱版エンドポイント（本番では絶対に有効にしない）
    seed_orders: int = 1_000_000
    seed_products: int = 100_000
    cors_origins: list[str] = ["http://localhost:8000"]

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
