# REST Client デモの解説

`clients/http/` の各台本（`.http`）について、リクエストごとの内容・期待されるレスポンス・説明のポイントをまとめたもの。台本と同じ名前のファイルが 1 対 1 で対応する。デモ中は、エディタで台本とこの解説を左右に並べて開くとよい。

| 台本 | 解説 | テキストの節 |
|---|---|---|
| `01-realtime-openapi.http` | [01-realtime-openapi.md](01-realtime-openapi.md) | 1-1 REST / SSE / WebSocket、1-3 OpenAPI |
| `01-2-graphql.http` | [01-2-graphql.md](01-2-graphql.md) | 1-1 GraphQL |
| `02-1-resources-status.http` | [02-1-resources-status.md](02-1-resources-status.md) | 2-1 リソース指向設計とステータスコード |
| `02-2-versioning.http` | [02-2-versioning.md](02-2-versioning.md) | 2-2 バージョニングと段階的非推奨 |
| `02-3-pagination.http` | [02-3-pagination.md](02-3-pagination.md) | 2-3 ページネーション・フィルタ・ソート |
| `02-4-problem-details.http` | [02-4-problem-details.md](02-4-problem-details.md) | 2-4 統一エラーレスポンス |
| `03-1-auth.http` | [03-1-auth.md](03-1-auth.md) | 3-1 認証 |
| `03-2-authorization.http` | [03-2-authorization.md](03-2-authorization.md) | 3-2 認可（BOLA / BFLA） |
| `03-3-ratelimit.http` | [03-3-ratelimit.md](03-3-ratelimit.md) | 3-3 レートリミット |
| `03-4-validation.http` | [03-4-validation.md](03-4-validation.md) | 3-4 入力バリデーション |
| `04-2-idempotency.http` | [04-2-idempotency.md](04-2-idempotency.md) | 4-2 冪等性 |
| `04-3-cache.http` | [04-3-cache.md](04-3-cache.md) | 4-3 キャッシュ戦略 |
| `04-4-resilience.http` | [04-4-resilience.md](04-4-resilience.md) | 4-4 タイムアウト・フォールバック |

## 共通の前提

- `make run` で API を起動しておく。台本を実行する前に `make reset` を実行し、データを初期状態に戻す。
- リクエストの上に表示される **Send Request** を、上から順に押していく。後半のリクエストは、前半のレスポンスを参照している。
- 解説中の ID・トークン・日時は、実行のたびに変わる。

## REST Client の記法

| 記法 | 意味 |
|---|---|
| `###` | リクエストの区切り。後ろの文字列は見出し（コメント）として表示される |
| `@api = {{$dotenv API_BASE_URL}}` | ファイル変数。`clients/http/.env` の値を読み込む（`make env-sync` / `make http-env` で生成） |
| `# @name alice` | そのリクエストに名前を付ける。`{{alice.response.body.$.access_token}}` のように、レスポンスの値を JSONPath で後続のリクエストから参照できる |
| `{{p.response.headers.ETag}}` | 名前付きリクエストのレスポンスヘッダーを参照する |
| `{{$timestamp 1 s}}` | 現在時刻 + 1 秒の UNIX 時刻（秒） |
| `X-REQUEST-TYPE: GraphQL` | ボディを GraphQL のクエリと変数として扱い、JSON に変換して送る |

名前付きリクエストを参照する側を先に実行すると、「Request 'alice' not found」などのエラーになる。その場合は、参照先のリクエスト（多くはログイン）を先に実行する。

## よくあるつまずき

| 症状 | 原因と対処 |
|---|---|
| 途中から 401 になる | Supabase のアクセストークンの有効期限（1 時間）切れ。ログインのリクエストを実行し直す |
| `{{$dotenv ...}}` が空になる | `clients/http/.env` がない。`make env-sync`（ローカル）または `make http-env`（クラウド）を実行する |
| 2 回目の実行で結果が変わる | 前回の実行でデータの状態が変わっている（キャンセル済み、冪等キーが記録済みなど）。`make reset` を実行する |
| 商品一覧が急に 429 になる | 3-3 のレートリミット（容量 10、毎秒 2 補充）は、商品一覧のすべてのリクエストで共有している。数秒待つ |
