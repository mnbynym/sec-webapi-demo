"""3-3 レートリミット：Redis + Lua によるトークンバケット

- 複数 API インスタンスでもカウンタを共有できるよう Redis に状態を置く
- 判定と更新を Lua スクリプトでアトミックに実行する
- レスポンスヘッダーは IETF ドラフト（RateLimit-Policy / RateLimit）に従う
"""

import math

from fastapi import Depends, Request, Response
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.deps import get_redis
from app.problems import ProblemException

TOKEN_BUCKET_LUA = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local cost     = tonumber(ARGV[3])
local t        = redis.call('TIME')
local now      = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data   = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1]) or capacity
local ts     = tonumber(data[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(capacity / rate * 1000) + 1000)
return {allowed, tostring(tokens)}
"""


def rate_limited(policy: str = "default", cost: int = 1):
    """エンドポイントに付与するレートリミット依存関数を作る"""

    async def dependency(
        request: Request,
        response: Response,
        redis: Redis = Depends(get_redis),
        s: Settings = Depends(get_settings),
    ) -> None:
        # キーの多層化：認証済みなら sub、未認証なら接続元 IP
        user = getattr(request.state, "user", None)
        ident = f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
        key = f"ratelimit:{policy}:{ident}"

        capacity, rate = s.rate_limit_capacity, s.rate_limit_refill_per_sec
        allowed, tokens_s = await redis.eval(TOKEN_BUCKET_LUA, 1, key, capacity, rate, cost)
        tokens = float(tokens_s)

        window = math.ceil(capacity / rate)
        reset = math.ceil((capacity - tokens) / rate)
        headers = {
            "RateLimit-Policy": f'"{policy}";q={capacity};w={window}',
            "RateLimit": f'"{policy}";r={math.floor(tokens)};t={reset}',
        }
        if not allowed:
            retry_after = max(1, math.ceil((cost - tokens) / rate))
            raise ProblemException(
                429,
                "rate-limited",
                f"{retry_after} 秒後に再試行してください",
                headers={**headers, "Retry-After": str(retry_after)},
            )
        response.headers.update(headers)

    return dependency
