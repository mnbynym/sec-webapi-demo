# 02-1-resources-status.http の解説

テキスト 2-1「リソース指向設計とステータスコード」に対応する。注文（PostgreSQL）と商品（MongoDB）の操作を通して、成功系（201 / 200 / 204）とエラー系（400 / 401 / 404 / 409 / 422）の返し分けを一通り確認する。

## 1. ログイン（alice / carol）

| | 内容 |
|---|---|
| 送信 | Supabase Auth の `/auth/v1/token?grant_type=password` に、メールとパスワードを POST する |
| 結果 | `access_token`、`refresh_token`、`expires_in` などを含む JSON が返る |
| 役割 | `# @name alice` で結果に名前を付け、以降のリクエストで `{{alice.response.body.$.access_token}}` として参照する |

alice は一般ユーザー（注文の作成とキャンセル）、carol は管理者（商品の登録と削除）として使う。

## 2. 201 Created + Location：注文の作成

```http
POST /v1/orders
{"items": [{"sku": "SKU-000010", "quantity": 2}], "note": "2-1 デモ"}
```

**レスポンス**

- ステータス：`201 Created`
- ヘッダー：`Location: /v1/orders/{id}`、`Cache-Control: no-store`
- ボディ：作成された注文。`id` は UUID v7、`status` は `pending`、`items[].unit_price` は MongoDB の商品価格、`total_yen` は合計金額。

**ポイント**

- 作成の成功は 200 ではなく 201 で返し、`Location` ヘッダーで新しいリソースの URI を示す。
- 単価はクライアントから受け取らず、サーバー側で商品マスタ（MongoDB）から取得している。クライアントが送った価格を信用しない設計。
- `id` は推測困難な UUID にし、連番の `order_no` は画面表示用に分けている（3-2 の BOLA に関連）。
- 裏では、注文の登録と `order.created` イベントの outbox への書き込みを、同じトランザクションでコミットしている（4-2 に関連）。
- 個人の注文情報なので `Cache-Control: no-store` を付け、共有キャッシュに保存させない（4-3）。

実装は `app/routers/orders.py` の `create_order` にある。

## 3. 200 OK：作成した注文を v2 で取得

```http
GET /v2/orders/{{created.response.body.$.id}}
```

- 直前のレスポンスの `id` を使って取得する。
- v2 では、金額が `total: {"amount": ..., "currency": "JPY"}` のオブジェクトになっている。
- `Location` は v1 の URI を返しているが、ここでは v2 で取得している。この差は 2-2（バージョニング）で扱う。

## 4. 201 Created：キャンセル（コントローラーリソース）

```http
POST /v1/orders/{id}/cancellation
```

**レスポンス**

- ステータス：`201 Created`
- ボディ：`{"order_id": ..., "status": "cancelled", "cancelled_at": ...}`

**ポイント**

- `POST /orders/{id}/cancel` のような動詞の URI にせず、「キャンセル」という名詞のサブリソースを作成する形でモデル化している。
- `PATCH /orders/{id}` で `{"status": "cancelled"}` を送る方法もある。ただしその方法だと、任意の状態遷移を許すかどうかの検証がサーバー側で複雑になる。キャンセルのように業務ルール（発送済みは不可など）を伴う操作は、専用のリソースにした方が意図が明確になる。
- `Location` は注文本体（`/v2/orders/{id}`）を指している。キャンセルリソース自体を取得する GET を用意していないため。厳密には、作成したリソース自身の URI を返すのが本来の形。

## 5. 409 Conflict：もう一度キャンセル

同じリクエストをもう一度送る。

- ステータス：`409 Conflict`
- ボディ：`type: /problems/order-not-cancellable`、`detail: 現在のステータスは cancelled です`

リクエストの形式は正しいが、リソースの現在の状態と矛盾するときは 409 を返す。422（入力値が不正）とは使い分ける。

## 6. 400 Bad Request：JSON の構文エラー

閉じ括弧が足りない JSON を送る。

- ステータス：`400 Bad Request`
- ボディ：`type: /problems/malformed-request`

サーバーがリクエストを解釈できない（JSON としてパースできない）場合は 400 を返す。

`Content-Type: application/json` を付けずに送ると、FastAPI はボディを JSON として解釈しようとせず、「ボディが必要」という検証エラーとして 422 を返す（FastAPI の実装上の挙動）。

## 7. 422 Unprocessable Content：制約違反

```http
{"items": [{"sku": "ABC", "quantity": 0}]}
```

- ステータス：`422`
- ボディ：`invalid_params` に 2 件のエラーが入る（`body.items.0.sku` の形式違反、`body.items.0.quantity` の下限違反）。

JSON としては正しいが、スキーマの制約（SKU の形式、数量 1〜99）を満たさない場合は 422 を返す。エラーを 1 件ずつではなく、まとめて返している点にも注目する（2-4 に関連）。

## 8. 401 Unauthorized：トークンなし

- ステータス：`401`
- ヘッダー：`WWW-Authenticate: Bearer realm="webapi-demo"`

401 は「認証されていない」（誰か分からない）ことを示し、`WWW-Authenticate` ヘッダーで認証方式を伝える。403（誰かは分かるが権限がない）とは区別する。403 の実例は 3-2 で確認する。

## 9. 404 Not Found：存在しない注文

- ステータス：`404`
- ボディ：`type: /problems/not-found`、`detail: 注文が見つかりません`

この API は、他人の注文を指定した場合も同じ 404 を返す。「存在するが権限がない」ことすら知らせないため（3-2 で実演する）。

## 10. 201 Created：商品の登録（管理者）

carol のトークンで `SKU-900001` を登録する。

- ステータス：`201`
- ヘッダー：`Location: /v1/products/SKU-900001`、`ETag: "SKU-900001-v1"`

`ETag` は 4-3（条件付きリクエスト・楽観的ロック）で使う。alice のトークンで同じリクエストを送ると、403 になることも確認できる。

## 11. 409 Conflict：同じ SKU の再登録

- ステータス：`409`
- ボディ：`detail: SKU-900001 は既に登録されています`

MongoDB の一意インデックス（`sku_unique`）違反の例外（`DuplicateKeyError`）を、409 に変換して返している。DB の例外メッセージをそのまま返さない点も 2-4 の論点になる。

## 12. 204 No Content：削除

- ステータス：`204`
- ボディ：なし

削除に成功し、返すべき内容がない場合は 204 を返す。

もう一度同じ DELETE を送ると 404 になる。DELETE は冪等なメソッドだが、冪等性とは「何度実行してもサーバーの状態が同じになる」ことを指し、レスポンスのステータスまで同じになるとは限らない。4-2（冪等性）で詳しく扱う。

## 片付け

このデモで作成した注文（キャンセル済み）が残るので、次のデモの前に `make reset` を実行する。
