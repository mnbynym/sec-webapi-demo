# MongoDB のデータを確認する

デモで使っている MongoDB の実際のデータ（商品カタログ、注文イベント、集計結果）と、インデックスや実行計画を確認する方法。

ローカルモード（既定）では、MongoDB は Codespace（または Dev Container）の中の Docker コンテナで動いている。接続先は `mongodb://localhost:27017`、データベース名は `webapi_demo`。

確認方法は次の 3 つ。画面で見やすいのは 1。

| 方法 | 向いている用途 |
|---|---|
| 1. VS Code の MongoDB 拡張機能 | ドキュメントの閲覧、インデックスの確認、クエリの実行 |
| 2. ターミナルの mongosh | コマンドで素早く確認する |
| 3. ブラウザの Atlas | クラウドモード（MongoDB Atlas に接続）の場合のみ |

## データベースの構成

| コレクション | 中身 | データが入るタイミング |
|---|---|---|
| `products` | 商品カタログ（既定 10 万件） | `make seed` |
| `order_events` | outbox から配信された注文イベント | `make relay` の実行中に注文を作成したとき（4-2） |
| `sales_by_sku` | 注文イベントから集計した SKU ごとの販売数 | 同上 |

### products のドキュメント

```json
{
  "_id": { "$oid": "…" },
  "sku": "SKU-000001",
  "name": "Compact Headphones 1",
  "category": "electronics",
  "price": 5520,
  "tags": [],
  "description": "Compact Headphones 1 の説明文",
  "created_at": { "$date": "2026-08-31T23:55:00Z" },
  "version": 1,
  "source": "seed"
}
```

| フィールド | 用途 |
|---|---|
| `sku` | 商品コード。一意インデックス（`sku_unique`）で重複を防ぐ（2-1 の 409） |
| `category`、`price`、`created_at` | 商品検索のフィルタとソート（2-3） |
| `version` | 更新のたびに 1 増える。ETag と楽観的ロックに使う（4-3） |
| `source` | `seed`（初期データ）か `api`（API で登録）か。`make reset` で `api` 由来のデータを消すために使う |

### order_events / sales_by_sku のドキュメント

```json
// order_events：処理済みのイベント ID を記録する（冪等コンシューマ）
{ "_id": "019a…（outbox のイベント ID）", "type": "order.created", "order_id": "019a…" }

// sales_by_sku：SKU ごとの販売数
{ "_id": "SKU-000030", "quantity": 1, "orders": 1 }
```

`order_events` は `_id` にイベント ID を使っているため、同じイベントが 2 回届いても 2 件目は一意制約で弾かれ、集計が二重に加算されない（4-2）。

### products のインデックス

| 名前 | キー | 用途 |
|---|---|---|
| `_id_` | `_id` | MongoDB の既定 |
| `sku_unique` | `sku`（一意） | SKU の重複防止、SKU による取得 |
| `esr_category_created_price` | `category` → `created_at` ↓ → `_id` ↓ → `price` | 商品検索（ESR ルールに沿った順序） |
| `ers_category_price_created` | `category` → `price` → `created_at` ↓ | 比較用（範囲条件を先に置いた順序） |
| `created_desc` | `created_at` ↓ → `_id` ↓ | カテゴリを指定しない一覧 |

## 1. VS Code の MongoDB 拡張機能

Dev Container の設定で「MongoDB for VS Code」拡張機能をインストール済み。

### 接続する

1. 左のアクティビティバーで、MongoDB の葉のアイコンをクリックする。
2. **Add Connection** → **Connect with Connection String** を選ぶ。
3. `mongodb://localhost:27017` を入力する。
4. 接続の一覧から `webapi_demo` を展開する。

コレクションを展開するとドキュメントの一覧が表示され、クリックすると JSON としてエディタに開く。コレクションの下の **Indexes** で、インデックスの定義も確認できる。

### Playground でクエリを実行する

`clients/mongodb/explore.mongodb.js` を開き、確認したい文を選択して、右上の実行ボタン（▷）を押す。結果は右側のペインに表示される。

新しく書く場合は、コマンドパレットで **MongoDB: Create MongoDB Playground** を実行する。

```javascript
use('webapi_demo');

// 1 件の中身
db.products.findOne({ sku: 'SKU-000001' });

// 件数とインデックス
db.products.countDocuments();
db.products.getIndexes();

// 2-3 の ESR インデックスの実行計画（make explain-mongo と同じ比較）
db.products
  .find({ category: 'books', price: { $gte: 1000, $lte: 5000 } })
  .sort({ created_at: -1, _id: -1 })
  .limit(20)
  .hint('esr_category_created_price')
  .explain('executionStats');

// 4-2 の冪等コンシューマの結果
db.sales_by_sku.find().sort({ quantity: -1 }).limit(5);
```

複数の文をまとめて実行すると、最後の文の結果だけが表示される。1 文ずつ選択して実行するとよい。

### 実行計画の読み方

`explain('executionStats')` の結果のうち、次の項目を確認する。`hint` を `ers_category_price_created` に変えて、2 つのインデックスを比べる。

| 項目 | 意味 | ESR | ERS |
|---|---|---|---|
| `executionStats.nReturned` | 返却件数 | 20 | 20 |
| `executionStats.totalKeysExamined` | 走査したインデックスのキー数 | 少ない（約 100） | 多い（約 2,500） |
| `executionStats.totalDocsExamined` | 読み込んだドキュメント数 | 20 | 約 2,500 |
| `queryPlanner.winningPlan` の `SORT` ステージ | メモリ上でのソート | なし | あり |

## 2. ターミナルの mongosh

コンテナの中の mongosh（MongoDB のシェル）で、コマンドを直接実行する。

```bash
docker compose exec mongo mongosh webapi_demo
```

```javascript
db.products.findOne()
db.products.countDocuments()
db.products.getIndexes()
db.order_events.find().limit(5)
```

`exit` で終了する。

## 3. ブラウザの Atlas（クラウドモードの場合）

MongoDB Atlas に接続するクラウドモードで動かしている場合は、Atlas の Web 画面（Data Explorer）でもデータを確認できる。ローカルモードでは、Atlas にはデータが入っていない。

クラウドモードへの切り替え手順は次のとおり（詳細は README の「クラウドモード」を参照）。

1. `.env` の `MONGODB_URI` を、Atlas の接続文字列に変える。
2. Atlas の **Network Access** に、接続元の IP アドレスを許可する。Codespace のグローバル IP は `curl -s ifconfig.me` で確認できる。
3. `make db-init seed` で Atlas にデータを投入し、`make run` を再起動する。

その後、Atlas のクラスター画面の **Browse Collections** で `webapi_demo` を開く。

注意点は次のとおり。

- Atlas の無料枠（M0）の容量は 512MB。商品 10 万件は収まるが、余裕は大きくない。容量が気になる場合は、`.env` の `SEED_PRODUCTS` を減らす。
- Codespace のグローバル IP は、起動のたびに変わることがある。接続できなくなったら、Network Access の許可を確認する。

## データを変更してしまった場合

拡張機能や mongosh ではデータを直接編集・削除できる。デモの結果が想定と変わってしまったら、`make reset` を実行する。API で登録した商品の削除、初期データの先頭 100 件の復元、`order_events` と `sales_by_sku` の削除が行われる。

それでも戻らない場合は、`uv run python -m scripts.seed --force` でデータを作り直す。
