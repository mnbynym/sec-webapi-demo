# CLAUDE.md（5-2 AI コーディングアシスタント向けのプロジェクト規約）

このリポジトリは WebAPI 研修のデモ。コードを生成・修正するときは以下に従う。

## 仕様
- API 仕様の正は `contracts/openapi.baseline.json`（OpenAPI 3.1）。エンドポイントを追加・変更したら
  `make openapi-export` で書き出し、`make oasdiff-current` で破壊的変更がないことを確認する
- 破壊的変更が必要な場合は `/v2` を新設し、旧エンドポイントに `deprecated_endpoint()` を付ける

## 実装規約
- エラーは必ず `ProblemException(status, slug, ...)` で送出する（RFC 9457）。新しい種類のエラーは
  `app/problems.py` の `PROBLEM_TYPES` に追加する
- リクエストモデルは `ConfigDict(extra="forbid")` を付けた専用 DTO にする。DB のモデルを直接バインドしない
- 所有者が存在するリソースは、ID だけで照会せず必ず `user_id` を WHERE 句に含める（BOLA 対策）
- 管理者機能は `Depends(require_admin)` を使う。ロールは JWT ではなく DB を正とする
- 一覧 API はカーソルベースのページネーションにし、ソートには一意キーのタイブレーカーを付ける
- すべてのエンドポイントに `summary` と、4xx/5xx の `responses=problem_responses(...)` を付ける
- `app/routers/vuln.py` のコードは参考にしない（意図的に脆弱）

## 確認コマンド
- `make lint`、`make lint-openapi`
- 動作確認は `clients/http/` に .http ファイルを追加して行う
