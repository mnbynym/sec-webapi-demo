"""デモユーザーのアクセストークンを標準出力に出す（シェルスクリプト用）

TOKEN=$(uv run python -m scripts.token alice)
"""

import sys

import httpx

from app.config import get_settings

s = get_settings()
user = sys.argv[1] if len(sys.argv) > 1 else "alice"
r = httpx.post(
    f"{s.supabase_url}/auth/v1/token",
    params={"grant_type": "password"},
    headers={"apikey": s.supabase_anon_key},
    json={"email": f"{user}@example.com", "password": s.demo_password},
    timeout=10,
)
r.raise_for_status()
print(r.json()["access_token"])
