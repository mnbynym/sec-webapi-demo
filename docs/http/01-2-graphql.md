# 01-2-graphql.http の解説

テキスト 1-1 の GraphQL に対応する。REST と同じ注文（PostgreSQL）・商品（MongoDB）のデータを GraphQL で公開し、「クライアントが必要な形を指定する」利点と、その裏返しである N+1 問題・DoS 対策を確認する。

- エンドポイントは `/graphql`（DataLoader あり）と `/graphql/naive`（DataLoader なし、比較用）の 2 つ。
- ブラウザで `http://localhost:8000/graphql` を開くと GraphiQL（スキーマの補完とドキュメント付きの IDE）が使える。
- 各レスポンスの `extensions.queryStats` に、そのリクエストで発行した DB クエリの回数を載せている（デモ用の拡張）。

実装は `app/graphql_api.py`、スキーマ（SDL）は `contracts/schema.graphql` にある。

## 1. ログイン（alice）

`myOrders` は認証が必要なので、alice のトークンを取得しておく。

## 2. 必要なフィールドだけを取得する

```graphql
query {
  products(category: "books", first: 3) { sku name price }
}
```

**レスポンス**

```json
{ "data": { "products": [ { "sku": "SKU-000008", "name": "…", "price": 7980 }, … ] } }
```

**ポイント**

- クエリに書いたフィールドだけが返る。REST では `?fields=sku,name,price`（Sparse Fieldsets）を API ごとに作り込む必要があったが、GraphQL では言語仕様として備わっている。
- エンドポイントは `/graphql` の 1 つだけで、何を取得するかはクエリで決まる。REST のようにリソースごとの URI はない。

## 3. 1 リクエストで複数のリソースを取得する

```graphql
query ($first: Int!) {
  myOrders(first: $first) {
    hasNextPage
    endCursor
    nodes { orderNo status items { quantity product { name price } } }
  }
  product(sku: "SKU-000001") { name price }
}
```

変数 `{"first": 3}` は、空行の後に JSON で書く。

**ポイント**

- 注文一覧、注文明細の商品情報、別の商品の詳細を 1 回のリクエストで取得している。REST なら「注文一覧 1 回 + 明細ごとの商品詳細 N 回 + 商品詳細 1 回」になる（アンダーフェッチ）。
- モバイルアプリのように通信の往復が高くつくクライアントや、画面ごとに必要なデータの形が違うフロントエンド（BFF）で効果が大きい。
- ページネーションは REST と同じカーソル方式（`endCursor` を次の `after` に渡す）にしている。
- `myOrders` は、REST と同じくトークンのユーザー ID で必ず絞り込む。GraphQL にしても、BOLA 対策は resolver（データを取得する関数）ごとに必要。

## 4. N+1 問題（DataLoader なし）

```http
POST /graphql/naive
```

```graphql
query {
  myOrders(first: 20) { nodes { orderNo items { quantity product { name price } } } }
}
```

**レスポンス**

```json
"extensions": { "queryStats": { "mongoQueries": 20, "postgresQueries": 1, "dataLoader": false } }
```

**ポイント**

- GraphQL の resolver は、フィールドごとに独立して実行される。`product` の resolver を素直に「SKU で 1 件取得」と書くと、注文 1 回（PostgreSQL）に対して、明細の数だけ MongoDB への問い合わせが発生する。これが N+1 問題。
- クライアントがクエリでネストを自由に組めるため、サーバー側では、どんな組み合わせで呼ばれても破綻しない実装が必要になる。

## 5. DataLoader あり

同じクエリを `/graphql` に送る。

**レスポンス**

```json
"extensions": { "queryStats": { "mongoQueries": 1, "postgresQueries": 1, "dataLoader": true } }
```

**ポイント**

- DataLoader は、同じタイミングで要求されたキー（SKU）を集めて、1 回の `$in` クエリにまとめる。重複したキーは 1 回だけ問い合わせる。
- DataLoader はリクエストごとに作り直す。ユーザーをまたいでキャッシュを共有すると、権限のないデータが混ざる危険がある。
- REST でも、一覧 API で関連データを返す場合は同じ問題が起きる（JOIN や一括取得で対処する）。GraphQL では、それがクライアントのクエリ次第で起きる点が違う。

## 6. エラー：未認証

```graphql
query { myOrders { nodes { orderNo } } }
```

**レスポンス**

- HTTP ステータス：`200 OK`

```json
{
  "data": null,
  "errors": [
    { "message": "認証が必要です", "path": ["myOrders"], "extensions": { "code": "UNAUTHENTICATED" } }
  ]
}
```

**ポイント**

- REST なら 401 を返す場面だが、GraphQL では HTTP は 200 のまま、`errors` 配列でエラーを返す。1 つのクエリの中で、成功したフィールドと失敗したフィールドが混在しうるため（部分的な成功）。
- エラーの種類は `extensions.code` で伝える。監視やアラートを HTTP ステータスだけで組んでいると、GraphQL のエラーを見逃す。
- 想定外の例外（DB の接続エラーなど）は、`MaskErrors` で「サーバー内部でエラーが発生しました」に置き換えている。REST の 500 で詳細を返さないのと同じ考え方だ（2-4）。

## 7. エラー：スキーマにないフィールド

```graphql
query { products(first: 1) { sku password } }
```

**レスポンス**

```json
"errors": [ { "message": "Cannot query field 'password' on type 'Product'. Did you mean …?" } ]
```

**ポイント**

- クエリは実行前にスキーマと照合され、存在しないフィールドは実行されずに拒否される。型付きのスキーマが契約になっている点は、OpenAPI と同じ。
- エラーメッセージに候補（Did you mean …）が出るのは開発時には便利だが、スキーマの推測を助けることにもなる。

## 8. 深さ制限

```graphql
query { products(first: 1) { related { related { related { related { related { name } } } } } } }
```

**レスポンス**

```json
"errors": [ { "message": "'anonymous' exceeds maximum operation depth of 5" } ]
```

**ポイント**

- `related`（同じカテゴリの商品）のように循環できる関係があると、クライアントはいくらでも深いクエリを書ける。1 段ごとに件数が掛け算で増えるので、1 回のリクエストでサーバーに巨大な負荷をかけられる。
- REST では 1 回のリクエストのコストをサーバーが決めるが、GraphQL ではクライアントが決める。そのため、実行前に深さ（ここでは 5 段まで）を検査して拒否する。

## 9. エイリアス数の制限

```graphql
query {
  a1: product(sku: "SKU-000001") { name }
  …
  a11: product(sku: "SKU-000011") { name }
}
```

**レスポンス**

```json
"errors": [ { "message": "11 aliases found. Allowed: 10" } ]
```

**ポイント**

- エイリアス（`a1:` のような別名）を使うと、同じフィールドを 1 つのクエリの中で何度でも実行できる。ログイン系のミューテーションを 1,000 個並べて、レートリミットをすり抜けて総当たりする攻撃が知られている。
- このデモでは、深さ（5）、エイリアス数（10）、トークン数（1,000）の 3 つの上限を設けている。本番では、クエリの複雑度（コスト）による制限や、事前登録したクエリだけを許可する方式（Persisted Queries）も検討する。

## 10. イントロスペクション

```graphql
query { __schema { queryType { fields { name description } } } }
```

**レスポンス**

- `products`、`product`、`myOrders` などのフィールド名と説明が返る。

**ポイント**

- GraphQL では、スキーマ全体をクエリで取得できる。GraphiQL の補完もこの仕組みで動いている。
- 外部公開する API では、内部構造の把握を助けるため、本番では `DisableIntrospection` で無効にすることが多い。

## 11. GET での実行

```http
GET /graphql?query=%7Bproduct(sku%3A%22SKU-000001%22)%7Bname%20price%7D%7D
```

URL エンコードを外すと `{ product(sku: "SKU-000001") { name price } }` になる。

**ポイント**

- GraphQL は通常 POST で送るため、HTTP のキャッシュ（ブラウザ、CDN）が効かない。REST の GET + ETag（4-3）のようなキャッシュ戦略を取りにくいのが GraphQL の弱点の一つ。
- 参照系のクエリなら GET でも送れるので、CDN でキャッシュさせることもできる。ただし URL が長くなるため、クエリのハッシュだけを送る Persisted Queries と組み合わせるのが一般的。
