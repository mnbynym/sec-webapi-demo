# 1-1 補足：GraphQL と gRPC

研修では実演せず、説明だけで済ませる場合を想定した資料。各項目に、デモを実行したときの出力例を載せてある（スライドにそのまま貼れる）。

## 3 方式の比較

| 観点 | REST | GraphQL | gRPC |
|---|---|---|---|
| 契約 | OpenAPI | SDL（`contracts/schema.graphql`） | `.proto`（`proto/inventory/v1/inventory.proto`） |
| エンドポイント | リソースごとに URI | 単一（`/graphql`） | サービス・メソッド単位 |
| 取得するデータの形 | サーバーが決める（`fields` で補助） | クライアントがクエリで決める | `.proto` のメッセージで固定 |
| データ形式 | JSON（テキスト） | JSON（テキスト） | Protobuf（バイナリ） |
| エラー | HTTP ステータス + RFC 9457 | HTTP 200 + `errors` 配列 | gRPC ステータスコード |
| HTTP キャッシュ | 効く（GET + ETag） | 基本的に効かない（POST） | 効かない |
| ブラウザから直接 | 呼べる | 呼べる | 呼べない（gRPC-Web / Connect が必要） |
| 主な用途 | 外部公開 API | 画面ごとに必要なデータが異なるフロントエンド（BFF） | マイクロサービス間通信 |

## GraphQL

起動は不要（`make run` の API に `/graphql` として同居している）。台本は `clients/http/01-2-graphql.http`、ブラウザで `/graphql` を開くと GraphiQL が使える。

### 必要なフィールドだけを 1 回で取得する

REST で「注文一覧と各注文の商品名」を表示するには、注文一覧を 1 回取得した後、商品詳細を明細の数だけ取得する必要がある（アンダーフェッチ）。逆に、一覧 API が画面で使わないフィールドまで返すのがオーバーフェッチだ。GraphQL では、クライアントが必要な形をクエリで指定する。

```graphql
query {
  myOrders(first: 3) {
    nodes { orderNo status items { quantity product { name price } } }
  }
}
```

### N+1 問題と DataLoader

上のクエリは、ナイーブに実装すると「注文一覧で 1 回 + 明細ごとに商品を 1 回」の問い合わせになる。レスポンスの `extensions.queryStats` で回数が見える。

```jsonc
// POST /graphql/naive（DataLoader なし、20 件）
"extensions": { "queryStats": { "mongoQueries": 20, "postgresQueries": 1, "dataLoader": false } }

// POST /graphql（DataLoader あり、同じクエリ）
"extensions": { "queryStats": { "mongoQueries": 1, "postgresQueries": 1, "dataLoader": true } }
```

DataLoader は、同じタイミングで要求された SKU を集めて 1 回の `$in` クエリにまとめる。DataLoader はリクエストごとに作り直す（ユーザー間でキャッシュを共有しない）。

### GraphQL 特有のセキュリティ対策

クライアントが自由にクエリを組めるため、1 回のリクエストでサーバーに大きな負荷をかけられる。実行前に、次のような上限で拒否する。

| 対策 | このデモの設定 | 拒否したときのメッセージ |
|---|---|---|
| 深さ制限 | 5 段 | `'anonymous' exceeds maximum operation depth of 5` |
| エイリアス数の制限 | 10 個 | `11 aliases found. Allowed: 10` |
| トークン数の制限 | 1,000 | クエリ文字列が大きすぎる場合に拒否 |

ほかに、本番ではイントロスペクションの無効化（`DisableIntrospection`）や、クエリの複雑度（コスト）による制限、事前登録したクエリだけを許可する方式（Persisted Queries）も検討する。

### エラーの返し方

```jsonc
// 未認証で myOrders を要求 → HTTP 200
{
  "data": null,
  "errors": [{ "message": "認証が必要です", "path": ["myOrders"], "extensions": { "code": "UNAUTHENTICATED" } }]
}
```

HTTP ステータスは 200 のまま、`errors[].extensions.code` でエラーの種類を伝える。監視で HTTP ステータスだけを見ているとエラーを見逃す点に注意する。想定外の例外は `MaskErrors` で内容を隠している（REST の 500 で詳細を返さないのと同じ考え方）。

## gRPC

```bash
make grpc-server    # ターミナル 1（:50051）
make grpc-demo      # ターミナル 2
```

### 契約とコード生成

`.proto` が契約そのものになる。`make grpc-gen` で、メッセージのクラスとクライアント・サーバーのスタブが生成される（`app/grpc_gen/`）。フィールド番号がシリアライズ後の識別子になるため、一度使った番号は変えず、削除したフィールドは `reserved` で予約する。これが gRPC における後方互換性の守り方だ（2-2 のバージョニングと対応する）。

### 実行結果の例

```text
=== Unary RPC：生成されたスタブを型付きのメソッドとして呼ぶ
sku: "SKU-000001"
available: 55
warehouse: WAREHOUSE_OSAKA
warehouse は enum: WAREHOUSE_OSAKA / 3.2 ms

=== エラー：HTTP ステータスではなく gRPC のステータスコードで返る
ABC          → INVALID_ARGUMENT  sku の形式が不正です: 'ABC'
SKU-999999   → NOT_FOUND         SKU-999999 は存在しません

=== デッドライン：クライアントが 0.5 秒を指定、サーバー処理は 2 秒かかる
DEADLINE_EXCEEDED after 2 ms

=== ペイロードサイズ：同じ 100 件の在庫を Protobuf と JSON で比較
                 raw      gzip
Protobuf       3,200       866
JSON          10,868       981

=== Server streaming RPC：1 回の呼び出しでサーバーから複数のメッセージを受け取る
09:49:26 available=52   delta=-3
09:49:26 available=49   delta=-3
09:49:27 available=47   delta=-2
```

説明のポイントは次のとおりだ。

- **デッドラインが標準機能になっている**：クライアントが指定したタイムアウトは `grpc-timeout` ヘッダーでサーバーに伝わり、サーバーは `context.time_remaining()` で残り時間を知る。4-4 で REST に `X-Request-Deadline` を自前で実装したことが、gRPC では最初から備わっている。
- **バイナリは小さい**：フィールド名を送らず、番号と型で表現するので約 1/3 になる。ただし gzip 後の差は小さい。サイズよりも、シリアライズの速さと型の厳密さの方が大きな利点だ。
- **ストリーミングが標準**：HTTP/2 上で、サーバーストリーミング・クライアントストリーミング・双方向ストリーミングを同じ仕組みで扱える。

### grpcurl（curl の gRPC 版）

Server Reflection を有効にしているので、`.proto` を持っていないクライアントでもサービスを調べて呼び出せる。

```bash
make grpcurl-list    # サービス・メソッドの一覧と定義
make grpcurl-get     # JSON で書いたリクエストを Protobuf に変換して呼び出す
```

Reflection は便利な反面、内部 API の構造をそのまま公開する。外部から到達できる環境では無効にすることが多い。

### ブラウザから直接呼べない理由

gRPC は HTTP/2 のトレーラー（レスポンス本文の後に送るヘッダー）でステータスを返すが、ブラウザの fetch API はトレーラーを扱えない。ブラウザから使う場合は gRPC-Web（Envoy などのプロキシで変換）や Connect プロトコルを使う。外部公開には REST、サービス間通信には gRPC という使い分けになる理由の一つだ。
