"""2-3 オフセット vs カーソル（Keyset）の実行計画比較（PostgreSQL）

    uv run python -m scripts.explain_pg [--offset 500000]

リモート DB の応答時間はネットワークで揺れるため、EXPLAIN ANALYZE の DB 内実行時間と
読み込んだバッファ数（= 走査量）で比較する。
"""

import argparse
import asyncio
import re

from app.config import get_settings
from app.db import create_pg_pool

COLS = "id, order_no, status, total_yen, created_at"


def summarize(title: str, plan: list[str]) -> None:
    text = "\n".join(plan)
    exec_ms = re.search(r"Execution Time: ([\d.]+) ms", text)
    buffers = re.search(r"Buffers: shared hit=(\d+)(?: read=(\d+))?", text)
    temp = re.search(r"temp read=(\d+) written=(\d+)", text)
    # 各ノードが処理した行数のうち最大のもの（= 実際に読んだ行数）
    rows = max((int(r) for r in re.findall(r"actual time=[\d.]+\.\.[\d.]+ rows=(\d+)", text)), default=0)
    print(f"\n=== {title}")
    for line in plan[:6]:
        print("  " + line)
    print("  ...")
    pages = int(buffers[1]) + int(buffers[2] or 0) if buffers else 0
    spill = f" / 一時ファイル {int(temp[2]):,} ページ書き込み" if temp else ""
    print(f"  ▶ 実行時間 {exec_ms[1] if exec_ms else '?'} ms / 走査ページ {pages:,} / 処理行数 {rows:,}{spill}")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--offset", type=int, default=500_000)
    args = p.parse_args()

    s = get_settings()
    pool = await create_pg_pool(s, max_size=1)
    try:
        alice = await pool.fetchval("select id from profiles where email = 'alice@example.com'")
        total = await pool.fetchval("select count(*) from orders where user_id = $1", alice)
        offset = min(args.offset, max(total - 21, 0))
        print(f"alice の注文 {total:,} 件、{offset:,} 件目以降の 20 件を取得する")

        async def explain(sql: str) -> list[str]:
            rows = await pool.fetch(f"explain (analyze, buffers) {sql}")
            return [r[0] for r in rows]

        where = f"user_id = '{alice}'"
        order = "order by created_at desc, id desc"
        summarize(
            "オフセット方式（LIMIT 20 OFFSET n）",
            await explain(f"select {COLS} from orders where {where} {order} limit 20 offset {offset}"),
        )

        # 同じ位置をカーソルで指定する
        c_at, c_id = await pool.fetchrow(
            f"select created_at, id from orders where {where} {order} limit 1 offset {max(offset - 1, 0)}"
        )
        summarize(
            "カーソル方式（WHERE (created_at, id) < (:c_at, :c_id) LIMIT 20）",
            await explain(
                f"select {COLS} from orders where {where} "
                f"and (created_at, id) < ('{c_at.isoformat()}', '{c_id}') {order} limit 20"
            ),
        )

        summarize("総件数（COUNT(*)）", await explain(f"select count(*) from orders where {where}"))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
