# 02-4-problem-details.http の解説

テキスト 2-4「統一エラーレスポンス（RFC 9457）」に対応する。入力検証エラー、業務エラー、存在しないリソースのどれも、同じ形式（Problem Details）で返ることを見せる。最後に、`request_id` と `trace_id` を使って、エラーをサーバーログやトレースと突き合わせる方法を見せる。

## Problem Details の構造

| メンバー | 内容 | このデモでの値の例 |
|---|---|---|
| `type` | エラーの種類を表す URI。同じ種類なら常に同じ値 | `/problems/validation-failed` |
| `title` | 種類ごとに固定の短い説明 | `入力内容に不備があります` |
| `status` | HTTP ステータス（レスポンスのステータスと同じ値） | `422` |
| `detail` | この発生事例に固有の説明 | `指定されたパラメータの値が制約を満たしていません` |
| `instance` | 発生したリクエストのパス | `/v1/orders` |
| 拡張メンバー | RFC で定義されていない独自の情報 | `request_id`、`trace_id`、`invalid_params` |

`Content-Type` は `application/problem+json` になる。クライアントは `type` で処理を分岐し、`title` や `detail` の文言には依存しない、という使い方を説明する。

## 1. ログイン（alice）

## 2. 422：複数の入力エラーをまとめて返す

```http
POST /v1/orders
{"items": [{"sku": "SKU-1", "quantity": 100}], "note": "x", "coupon": "FREE"}
```

**レスポンス**

```json
{
  "type": "/problems/validation-failed",
  "status": 422,
  "title": "入力内容に不備があります",
  "detail": "指定されたパラメータの値が制約を満たしていません",
  "instance": "/v1/orders",
  "request_id": "…",
  "invalid_params": [
    { "name": "body.items.0.sku", "reason": "String should match pattern '^SKU-\\d{6}$'" },
    { "name": "body.items.0.quantity", "reason": "Input should be less than or equal to 99" },
    { "name": "body.coupon", "reason": "Extra inputs are not permitted" }
  ]
}
```

**説明のポイント**

- 3 つの誤り（SKU の形式、数量の上限、未定義の `coupon`）を 1 回のレスポンスでまとめて返している。1 件ずつ返すと、クライアントは直しては送り直すことを何度も繰り返すことになる。
- `invalid_params` は RFC 9457 の例で使われている拡張メンバーの名前だ。`name` はエラーの場所、`reason` は理由を表す。
- `coupon` のような未定義のプロパティも拒否している（`extra="forbid"`）。3-4 の Mass Assignment 対策と同じ仕組みだ。
- `reason` は Pydantic が生成する英語のメッセージのままにしている。画面に表示するなら、`name` と種類をもとにクライアント側で文言を組み立てる方がよい。

実装は `app/problems.py` の `install_problem_handlers` にある。FastAPI 標準の 422（`{"detail": [...]}`）を、Problem Details の形式に変換している。

## 3. 422：業務エラーも同じ形式で返す

```http
POST /v1/orders
{"items": [{"sku": "SKU-999999", "quantity": 1}]}
```

**レスポンス**

```json
{
  "type": "/problems/unknown-sku",
  "status": 422,
  "title": "存在しない商品が指定されました",
  "instance": "/v1/orders",
  "request_id": "…",
  "invalid_params": [ { "name": "items.sku", "reason": "SKU-999999 は存在しません" } ]
}
```

**説明のポイント**

- 形式としては正しいが、商品マスタに存在しない SKU という業務上のエラーだ。ステータスは同じ 422 でも、`type` が違うのでクライアントは区別できる。
- エラーの種類は `app/problems.py` の `PROBLEM_TYPES` に一覧として定義している。種類が増えても、形式はばらばらにならない。

## 4. type の URI はドキュメントとして参照できる

```http
GET /problems/validation-failed
```

- エラーの説明ページ（HTML）が返る。
- `type` には、開発者がブラウザで開けば意味が分かる URI を使うのが推奨されている。RFC ではエラーの種類が特にない場合の値として `about:blank` が定められており、このデモでも未定義のステータスにはそれを使っている。

## 5. request_id / trace_id でログと突き合わせる

```http
GET /v2/orders/01900000-0000-7000-8000-000000000000
X-Request-Id: demo-2-4-0001
```

**レスポンス**

- ステータス：`404`
- ヘッダー：`X-Request-Id: demo-2-4-0001`
- ボディ：`"request_id": "demo-2-4-0001"`、`"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"`（OpenTelemetry を有効にしている場合）

**説明のポイント**

- クライアントが `X-Request-Id` を送ればその値を使い、送らなければサーバーが採番する。同じ ID がレスポンスのヘッダー、ボディ、サーバーログのすべてに残る。
- 問い合わせを受けたら `request_id` でサーバーログを検索すれば、該当するリクエストにたどり着ける。`make run` のターミナルに出ている JSON ログを見せるとよい。
- `trace_id` があれば、Grafana（Tempo）でそのリクエストのトレースを開ける（5-3）。
- 500 エラーの場合、スタックトレースや SQL の例外はレスポンスに含めず、ログにだけ残している。レスポンスには `request_id` だけを返し、詳細はサーバー側で調べる運用にする。内部情報の漏洩を防ぐためだ。

## 補足

- このデモではデータを変更しないので、`make reset` は不要。
- ほかの台本のエラーレスポンスも、すべてこの形式で返っている。2-1 の 409、3-3 の 429、4-4 の 503 / 504 なども見比べるとよい。
