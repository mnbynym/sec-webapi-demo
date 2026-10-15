"""3-1 認証：Supabase Auth が発行した JWT を API 側で検証する

- 非対称鍵（ES256 / RS256）: JWKS（/auth/v1/.well-known/jwks.json）から公開鍵を取得して検証
- HS256（レガシー共有鍵）   : SUPABASE_JWT_SECRET で検証
- ロール（admin 等）は JWT ではなく DB（profiles）を正とする（3-2 BFLA 対策）
"""

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from uuid import UUID

import asyncpg
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.deps import get_pool
from app.problems import ProblemException

bearer = HTTPBearer(auto_error=False, description="Supabase Auth のアクセストークン")

_WWW_AUTH = {"WWW-Authenticate": 'Bearer realm="webapi-demo"'}


@dataclass
class AuthUser:
    id: UUID
    email: str | None
    alg: str
    kid: str | None
    claims: dict[str, Any] = field(repr=False)


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    # 公開鍵はキャッシュされ、未知の kid が来たときだけ再取得される（鍵ローテーション対応）
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def _unauthorized(detail: str) -> ProblemException:
    return ProblemException(401, "unauthorized", detail, headers=_WWW_AUTH)


async def verify_token(token: str, s: Settings) -> AuthUser:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise _unauthorized("トークンの形式が不正です") from e

    alg = header.get("alg")
    if alg not in s.jwt_allowed_algs:  # alg=none や想定外アルゴリズムを拒否
        raise _unauthorized(f"許可されていない署名アルゴリズムです: {alg}")

    if alg == "HS256":
        if not s.supabase_jwt_secret:
            raise _unauthorized("HS256 トークンを検証する鍵が設定されていません")
        key: Any = s.supabase_jwt_secret
    else:
        try:
            signing_key = await asyncio.to_thread(_jwks_client(s.jwks_url).get_signing_key_from_jwt, token)
        except jwt.PyJWTError as e:
            raise _unauthorized("署名鍵を取得できません") from e
        key = signing_key.key

    try:
        claims = jwt.decode(token, key, algorithms=[alg], audience=s.jwt_audience, options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as e:
        raise _unauthorized("トークンの有効期限が切れています") from e
    except jwt.PyJWTError as e:
        raise _unauthorized("トークンを検証できません") from e

    return AuthUser(id=UUID(claims["sub"]), email=claims.get("email"), alg=alg, kid=header.get("kid"), claims=claims)


async def current_user(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: Settings = Depends(get_settings),
) -> AuthUser:
    if cred is None:
        raise _unauthorized("Authorization: Bearer <token> ヘッダーが必要です")
    user = await verify_token(cred.credentials, s)
    request.state.user = user
    return user


async def require_admin(user: AuthUser = Depends(current_user), pool: asyncpg.Pool = Depends(get_pool)) -> AuthUser:
    role = await pool.fetchval("select role from profiles where id = $1", user.id)
    if role != "admin":
        raise ProblemException(403, "forbidden", "管理者権限が必要です")
    return user
