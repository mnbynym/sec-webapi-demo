"""環境変数の同期

    uv run python -m scripts.sync_env --supabase   # ローカル Supabase の接続情報を .env に書き込む
    uv run python -m scripts.sync_env              # REST Client 用の clients/http/.env だけ生成する

clients/http/.env は REST Client の {{$dotenv ...}} から参照される（.gitignore 済み）。
"""

import argparse
import re
import subprocess
import sys

from scripts.envfile import HTTP_ENV, ensure_env, read_env, update_env

# supabase status -o env の出力キー → このリポジトリの環境変数（新旧 CLI の両方に対応）
MAPPING = {
    "SUPABASE_URL": ["API_URL"],
    "SUPABASE_ANON_KEY": ["ANON_KEY", "PUBLISHABLE_KEY"],
    "SUPABASE_SERVICE_ROLE_KEY": ["SERVICE_ROLE_KEY", "SECRET_KEY"],
    "SUPABASE_JWT_SECRET": ["JWT_SECRET"],
    "DATABASE_URL": ["DB_URL"],
}


def supabase_status() -> dict[str, str]:
    try:
        out = subprocess.run(
            ["npx", "--no-install", "supabase", "status", "-o", "env"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        sys.exit(f"supabase status に失敗しました。make supabase-start を先に実行してください: {e}")
    return {m[1]: m[2] for m in re.finditer(r'^([A-Z0-9_]+)="?([^"\n]*)"?$', out, re.M)}


def sync_supabase() -> None:
    status = supabase_status()
    updates = {"APP_MODE": "local", "DB_STATEMENT_CACHE_SIZE": "100"}
    for ours, candidates in MAPPING.items():
        for c in candidates:
            if status.get(c):
                updates[ours] = status[c]
                break
    update_env(updates)
    print("updated .env:", ", ".join(sorted(updates)))


def write_http_env() -> None:
    env = read_env()
    values = {
        "API_BASE_URL": env.get("API_BASE_URL", "http://localhost:8000"),
        "SUPABASE_URL": env.get("SUPABASE_URL", ""),
        "SUPABASE_ANON_KEY": env.get("SUPABASE_ANON_KEY", ""),
        "DEMO_PASSWORD": env.get("DEMO_PASSWORD", "Passw0rd!demo"),
    }
    HTTP_ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    print(f"wrote {HTTP_ENV.relative_to(HTTP_ENV.parents[2])}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--supabase", action="store_true")
    args = p.parse_args()
    ensure_env()
    if args.supabase:
        sync_supabase()
    write_http_env()
