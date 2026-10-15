""".env ファイルの読み書き（既存のコメントや並びを保ったまま値だけ更新する）"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
HTTP_ENV = ROOT / "clients" / "http" / ".env"


def ensure_env() -> None:
    if not ENV.exists():
        shutil.copy(EXAMPLE, ENV)
        print("created .env from .env.example")


def read_env(path: Path = ENV) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m:
            values[m[1]] = m[2].strip().strip('"').strip("'")
    return values


def update_env(updates: dict[str, str], path: Path = ENV) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    done = set()
    for idx, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m[1] in updates:
            lines[idx] = f"{m[1]}={updates[m[1]]}"
            done.add(m[1])
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    path.write_text("\n".join(lines) + "\n")
