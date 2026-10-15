"""2-2 段階的非推奨（Deprecation / Sunset / Link）と計画停止（Brownout）"""

from datetime import UTC, datetime
from email.utils import format_datetime

from fastapi import Depends, Request, Response
from redis.asyncio import Redis

from app.deps import get_redis
from app.problems import ProblemException


def deprecated_endpoint(name: str, deprecated_at: datetime, sunset_at: datetime, successor: str):
    """非推奨エンドポイントに付ける依存関数

    - Deprecation: @<unix-time>（RFC 9745）
    - Sunset: <HTTP-date>（RFC 8594）
    - Link: rel="deprecation"（移行ガイド）/ rel="successor-version"
    - 利用状況を Redis に記録（誰がまだ旧 API を使っているかの追跡）
    - Redis に brownout:<name> があれば 503 を返す（計画停止）
    """

    deprecation = f"@{int(deprecated_at.timestamp())}"
    sunset = format_datetime(sunset_at.astimezone(UTC), usegmt=True)

    async def dependency(request: Request, response: Response, redis: Redis = Depends(get_redis)) -> None:
        if await redis.exists(f"brownout:{name}"):
            raise ProblemException(
                503,
                "brownout",
                f"このエンドポイントは {sunset} に廃止されます。{successor} へ移行してください",
                headers={"Retry-After": "600", "Deprecation": deprecation, "Sunset": sunset},
            )
        user = getattr(request.state, "user", None)
        consumer = f"user:{user.id}" if user else f"ip:{request.client.host if request.client else '-'}"
        await redis.hincrby(f"deprecation:usage:{name}", consumer, 1)

        response.headers["Deprecation"] = deprecation
        response.headers["Sunset"] = sunset
        response.headers["Link"] = (
            f'</docs/migration-v2>; rel="deprecation"; type="text/html", <{successor}>; rel="successor-version"'
        )

    return dependency
