"""ユーザー / 管理者 API

- 3-1 JWT 検証結果の確認（whoami）
- 3-2 BFLA 対策（管理者 API はロールを DB で確認）
- 3-4 Mass Assignment 対策（更新可能なフィールドだけを持つ DTO + extra="forbid"）
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from app.auth import AuthUser, current_user, require_admin
from app.deps import get_pool, get_redis
from app.problems import ProblemException, problem_responses

router = APIRouter(tags=["users"])
admin = APIRouter(prefix="/v1/admin", tags=["admin"])


class WhoAmI(BaseModel):
    sub: UUID
    email: str | None
    alg: str = Field(description="署名アルゴリズム（ES256/RS256 は JWKS、HS256 は共有鍵で検証）")
    kid: str | None
    aud: Any
    issuer: str | None
    expires_at: datetime


class Profile(BaseModel):
    id: UUID
    email: str
    display_name: str
    role: Literal["member", "admin"]
    created_at: datetime


class ProfileUpdate(BaseModel):
    """更新を許可するフィールドだけを定義したリクエスト DTO（role は含めない）"""

    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=50)


@router.get(
    "/v1/auth/whoami", response_model=WhoAmI, summary="トークンの検証結果を確認する", responses=problem_responses(401)
)
async def whoami(user: AuthUser = Depends(current_user)):
    c = user.claims
    return WhoAmI(
        sub=user.id,
        email=user.email,
        alg=user.alg,
        kid=user.kid,
        aud=c.get("aud"),
        issuer=c.get("iss"),
        expires_at=datetime.fromtimestamp(c["exp"], tz=UTC),
    )


async def _profile(pool: asyncpg.Pool, user_id: UUID) -> Profile:
    row = await pool.fetchrow("select id, email, display_name, role, created_at from profiles where id=$1", user_id)
    if row is None:
        raise ProblemException(404, "not-found", "プロフィールがありません（make seed を実行してください）")
    return Profile(**dict(row))


@router.get(
    "/v1/users/me",
    response_model=Profile,
    summary="自分のプロフィール",
    tags=["users"],
    responses=problem_responses(401, 404),
)
async def get_me(user: AuthUser = Depends(current_user), pool: asyncpg.Pool = Depends(get_pool)):
    return await _profile(pool, user.id)


@router.patch(
    "/v1/users/me",
    response_model=Profile,
    summary="自分のプロフィールを更新する（安全版）",
    description="`role` などの未定義プロパティを送ると 422 で拒否する。",
    responses=problem_responses(401, 404, 422),
)
async def patch_me(body: ProfileUpdate, user: AuthUser = Depends(current_user), pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute("update profiles set display_name=$2 where id=$1", user.id, body.display_name)
    return await _profile(pool, user.id)


@admin.get(
    "/users",
    response_model=list[Profile],
    summary="全ユーザー一覧（管理者のみ）",
    responses=problem_responses(401, 403),
)
async def admin_list_users(_: AuthUser = Depends(require_admin), pool: asyncpg.Pool = Depends(get_pool)):
    rows = await pool.fetch("select id, email, display_name, role, created_at from profiles order by email")
    return [Profile(**dict(r)) for r in rows]


@admin.get(
    "/deprecation-usage",
    summary="非推奨 API の利用状況（2-2 移行ログの追跡）",
    responses=problem_responses(401, 403),
)
async def deprecation_usage(_: AuthUser = Depends(require_admin), redis: Redis = Depends(get_redis)):
    result: dict[str, dict[str, int]] = {}
    async for key in redis.scan_iter("deprecation:usage:*"):
        result[key.removeprefix("deprecation:usage:")] = {k: int(v) for k, v in (await redis.hgetall(key)).items()}
    return result
