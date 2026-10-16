# 04-3-cache.http の解説

テキスト 4-3「キャッシュ戦略」に対応する。商品詳細 API で、サーバー側のキャッシュ（Redis の Cache-Aside）と、HTTP のキャッシュ（Cache-Control、ETag、条件付きリクエスト）の両方を確認する。後半は、ETag を楽観的ロックに使った更新の競合検出を確認する。

**このファイルは、`make reset` をしてから実行する。** Redis のキャッシュが空の状態から始めないと、1 回目が MISS にならない。

## 2 種類のキャッシュ

| | サーバー側（Redis） | HTTP（クライアント・CDN） |
|---|---|---|
| 目的 | DB の負荷を減らす | 通信そのものを減らす |
| 制御 | API の実装（Cache-Aside） | レスポンスヘッダー（`Cache-Control`、`ETag`） |
| このデモでの確認 | `X-Cache: HIT / MISS` ヘッダー | `304 Not Modified` |

`X-Cache` はデモ用に付けているヘッダーで、Redis から返したか（HIT）、MongoDB から読んだか（MISS）を示す。

## 1. ログイン（carol）

後半の更新（管理者のみ）で使う。

## 2. 1 回目：MISS

```http
GET /v1/products/SKU-000005
```

`# @name p` で名前を付け、後続のリクエストで `{{p.response.headers.ETag}}` として ETag を参照する。

**レスポンス**

- ステータス：`200`
- ヘッダー：

```http
ETag: "SKU-000005-v1"
Cache-Control: public, max-age=60, stale-while-revalidate=300
X-Cache: MISS
```

**ポイント**

- Redis になかったので MongoDB から読み、Redis に保存してから返している（Cache-Aside）。
- `Cache-Control: public, max-age=60` は、ブラウザや CDN が 60 秒間はサーバーに問い合わせずに再利用してよい、という指示。`stale-while-revalidate=300` は、期限切れ後の 300 秒間は古い内容を返しつつ、裏で更新してよいことを示す。
- `ETag` は、このリソースのバージョンを表す識別子。このデモでは SKU と更新のたびに増える `version` から作っている。

## 3. 2 回目：HIT

- ヘッダー：`X-Cache: HIT`
- MongoDB にアクセスせず、Redis から返している。

## 4. 条件付きリクエスト：304 Not Modified

```http
GET /v1/products/SKU-000005
If-None-Match: {{p.response.headers.ETag}}
```

**レスポンス**

- ステータス：`304 Not Modified`
- ボディ：なし

**ポイント**

- クライアントは、手元にあるキャッシュの ETag を `If-None-Match` で送る。サーバー側の ETag と一致すれば、ボディを送らずに 304 だけを返す。クライアントは手元のキャッシュをそのまま使う。
- 内容が大きいほど、転送量の削減効果が大きい。
- ブラウザは、`max-age` が切れた後の再検証で、この条件付きリクエストを自動で送る。

## 5. 更新（If-Match による楽観的ロック）

```http
PATCH /v1/products/SKU-000005
Content-Type: application/merge-patch+json
If-Match: {{p.response.headers.ETag}}

{"price": 1234}
```

**レスポンス**

- ステータス：`200`
- ヘッダー：`ETag: "SKU-000005-v2"`（版が上がる）
- ボディ：`price` が 1234 になった商品

**ポイント**

- `If-Match` で、「取得したときの版（v1）のままなら更新する」という条件を付けている。
- `application/merge-patch+json`（RFC 7396）は、送ったフィールドだけを更新する形式。PUT（全体の置き換え）と違い、他のフィールドを送る必要がない。
- 更新と同時に、Redis のキャッシュを削除している（キャッシュの無効化）。次の GET は MongoDB から最新の内容を読む。

## 6. 古い ETag で再度更新：412 Precondition Failed

5 と同じ `If-Match`（v1 のまま）で、別の価格に更新しようとする。

**レスポンス**

- ステータス：`412 Precondition Failed`
- ボディ：`type: /problems/precondition-failed`、`detail: 他のリクエストで更新されています。再取得してください`

**ポイント**

- 2 人の管理者が同じ商品の画面を開き、それぞれ更新した状況を再現している。後から更新した方は、先に更新された内容を知らないまま上書きしようとしている。
- `If-Match` がなければ、後の更新が先の更新を黙って上書きする（Lost Update）。412 を返すことで、クライアントに再取得と再編集を促せる。
- ロックを取らずに、更新時に版を比べて競合を検出する方式を楽観的ロックという。DB では `version` が一致する場合だけ更新する条件付きの更新（`find_one_and_update`）で実装している。

## 7. 古い ETag で条件付き GET：200

```http
GET /v1/products/SKU-000005
If-None-Match: {{p.response.headers.ETag}}
```

- ステータス：`200`（304 ではない）
- ヘッダー：`ETag: "SKU-000005-v2"`、`X-Cache: MISS`

手元のキャッシュ（v1）は古くなったので、最新の内容（v2）が返る。5 の更新でキャッシュを削除したので、Redis からではなく MongoDB から読んでいる（MISS）。

## 8. ネガティブキャッシュ：存在しない商品

```http
GET /v1/products/SKU-999999
```

同じリクエストを 2 回送る。

- 1 回目：`404`、`X-Cache: MISS`
- 2 回目：`404`、`X-Cache: HIT`

**ポイント**

- 存在しないキーへのリクエストは、普通のキャッシュでは毎回 DB まで届いてしまう。存在しない ID を大量に送られると、キャッシュを素通りして DB に負荷が集中する（キャッシュペネトレーション）。
- 「存在しない」という結果も短い期間（30 秒）キャッシュすることで、DB への到達を防いでいる。期間を短くしているのは、その後に商品が登録された場合に反映を遅らせないため（商品の登録時にも、このキャッシュを削除している）。

## 9. 個人データは共有キャッシュさせない

```http
GET /v1/orders?limit=1
```

- ヘッダー：`Cache-Control: no-store`

注文のような個人のデータを `public` にすると、CDN やプロキシに保存され、別のユーザーに返ってしまう事故が起きうる。認証が必要なレスポンスは `private` か `no-store` にする。

## 実装上の補足

`app/routers/products.py` の `load_product` では、次の対策も実装している。

| 対策 | 防ぐ問題 |
|---|---|
| TTL にランダムな揺らぎ（±10%）を付ける | 同時に保存した大量のキーが同時に失効し、DB にアクセスが集中する（キャッシュアバランシェ） |
| キャッシュの再構築を 1 リクエストだけに任せる（Redis のロック） | 人気のキーが失効した瞬間に、大量のリクエストが一斉に DB を読みに行く（キャッシュスタンピード） |

## 片付け

```bash
make reset
```

`SKU-000005` の価格と版が元に戻り、Redis のキャッシュも消える。
