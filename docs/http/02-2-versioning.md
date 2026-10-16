# 02-2-versioning.http の解説

テキスト 2-2「バージョニングと段階的非推奨」に対応する。注文取得 API の v1（非推奨）と v2 を並べ、破壊的変更を v2 で吸収する方法と、v1 を安全に廃止するまでの手順（非推奨の通知、利用状況の追跡、計画停止）を見せる。

## v1 と v2 の違い

| | v1（`GET /v1/orders/{id}`） | v2（`GET /v2/orders/{id}`） |
|---|---|---|
| 金額 | `"total_yen": 5520`（整数） | `"total": {"amount": 5520, "currency": "JPY"}` |
| 状態 | 非推奨（2026-07-01）、廃止予定（2027-03-31） | 現行 |

金額の型を整数からオブジェクトに変えるのは、既存クライアントのパース処理を壊す破壊的変更だ。そのため v1 を残したまま v2 を新設している。

## 1. ログイン（bob / carol）

bob は注文の取得、carol は利用状況の確認（管理者 API）に使う。

## 2. bob の注文一覧から ID を取得

```http
GET /v1/orders?limit=1
```

最新の注文 1 件を取得し、`# @name list` で後続のリクエストから `{{list.response.body.$.data[0].id}}` として参照する。

## 3. v1（非推奨）で取得

**レスポンス**

- ステータス：`200 OK`（まだ使える）
- ヘッダー：

```http
Deprecation: @1782864000
Sunset: Wed, 31 Mar 2027 00:00:00 GMT
Link: </docs/migration-v2>; rel="deprecation"; type="text/html", </v2/orders>; rel="successor-version"
```

- ボディ：`total_yen` が整数で返る。

**説明のポイント**

- `Deprecation`（RFC 9745）は、非推奨になった日時を `@` + UNIX 時刻で示す。`@1782864000` は 2026-07-01 00:00 UTC だ。
- `Sunset`（RFC 8594）は、廃止（応答しなくなる）予定日時を HTTP-date で示す。
- `Link` で、移行ガイド（`rel="deprecation"`）と後継バージョン（`rel="successor-version"`）の場所を伝える。
- ヘッダーで通知するので、クライアントは実装を変えずに、ログやモニタリングで非推奨 API の利用を検知できる。
- OpenAPI でも `deprecated: true` を付けている。Swagger UI（`/docs`）では、このエンドポイントが取り消し線付きで表示される。

実装は `app/deprecation.py` の `deprecated_endpoint` にある。

## 4. v2 で取得

- ステータス：`200 OK`
- 非推奨のヘッダーは付かない。
- ボディ：`total` が `{amount, currency}` のオブジェクトで返る。

v1 と v2 は同じデータを返しており、違いはレスポンスの表現だけだ。内部のロジックとデータは共通にし、バージョンごとの差はレスポンスへの変換（`to_v1` / `to_v2`）だけに閉じ込めている。

## 5. 移行ガイド

```http
GET /docs/migration-v2
```

`Link: rel="deprecation"` が指すページだ。何がどう変わったかを人間向けに説明する。

## 6. 非推奨 API の利用状況（管理者）

```http
GET /v1/admin/deprecation-usage
```

**レスポンス**

```json
{ "v1-get-order": { "user:<bob の ID>": 1 } }
```

**説明のポイント**

- v1 が呼ばれるたびに、呼び出し元（ユーザー ID、未認証なら IP）ごとの回数を Redis に記録している。
- 廃止前に「誰がまだ v1 を使っているか」を把握し、個別に移行を依頼するための仕組みだ。利用がゼロになったことを確認してから廃止する。

## 7. 計画停止（Brownout）

ターミナルで計画停止を有効にしてから、v1 の取得を再実行する。

```bash
make brownout-on
```

**レスポンス**

- ステータス：`503 Service Unavailable`
- ヘッダー：`Retry-After: 600`、`Deprecation`、`Sunset`
- ボディ：`type: /problems/brownout`、`detail: このエンドポイントは … に廃止されます。/v2/orders へ移行してください`

**説明のポイント**

- 廃止日の前に、短時間だけ意図的に v1 を止める手法だ。ヘッダーの通知に気付いていない利用者に、廃止前に影響を体感させ、移行を促す。
- 止める時間を少しずつ長くしていき、問い合わせや障害が出ないことを確認してから本当に廃止する。
- このデモでは Redis のキー（`brownout:v1-get-order`）の有無で切り替えている。600 秒で自動的に解除される。

デモが終わったら解除する。

```bash
make brownout-off
```

## 関連するデモ

- 破壊的変更を機械的に検出する方法は、5-1 の `make oasdiff` で見せる（フィールドの削除、必須パラメータの追加、型の変更を検出する）。
