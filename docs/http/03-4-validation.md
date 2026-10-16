# 03-4-validation.http の解説

テキスト 3-4「入力バリデーションとペイロード汚染」に対応する。Mass Assignment（リクエストに余計なプロパティを混ぜて、変更されてはならない項目を書き換える攻撃）を、脆弱版と安全版で比べる。あわせて、正規表現の入力のエスケープ（ReDoS 対策）と、JavaScript のプロトタイプ汚染を確認する。

**このデモは alice を管理者に昇格させる。終わったら必ず `make reset` を実行する。**

## 1. ログイン（alice）

## 2. 現在のプロフィール

```http
GET /v1/users/me
```

**レスポンス**

```json
{ "id": "…", "email": "alice@example.com", "display_name": "Alice", "role": "member", "created_at": "…" }
```

`role` が `member`（一般ユーザー）であることを確認しておく。

## 3. ✔ 安全版：role の混入を拒否する

```http
PATCH /v1/users/me
{"display_name": "Alice ★", "role": "admin"}
```

**レスポンス**

- ステータス：`422`
- ボディ：`invalid_params: [{"name": "body.role", "reason": "Extra inputs are not permitted"}]`

**ポイント**

- 安全版のリクエストモデル（DTO）は、更新を許可する `display_name` だけを持ち、`extra="forbid"` で未定義のプロパティを拒否している。OpenAPI では `additionalProperties: false` として表現される。
- リクエスト全体を拒否しているので、`display_name` も更新されない。黙って `role` だけを無視する方法もあるが、拒否した方が、攻撃の試みやクライアントの誤りに気付きやすい。
- DB のテーブルやドメインモデルを、そのままリクエストの受け口に使わないことが本質。「読み取り用」「作成用」「更新用」で別のモデルを定義する。

実装は `app/routers/users.py` の `ProfileUpdate` と `patch_me` にある。

## 4. ✖ Mass Assignment（脆弱版）：受け取った JSON をそのまま反映

```http
PATCH /vuln/users/me
{"display_name": "Alice ★", "role": "admin"}
```

**レスポンス**

- ステータス：`200 OK`
- ボディ：`"display_name": "Alice ★", "role": "admin"`

**ポイント**

- 脆弱版は、受け取った JSON のキーのうち、テーブルに存在する列をすべて更新している。ORM の「リクエストの値をモデルに一括で代入する」機能を、許可リストなしで使った場合と同じ状態。
- 画面のフォームに `role` の入力欄がなくても、攻撃者は API に直接リクエストを送れる。画面にない項目は送られてこない、という前提は成り立たない。
- 実際に、GitHub（2012 年、Rails の Mass Assignment）などで、この種の脆弱性による権限昇格が起きている。

## 5. 昇格した alice が管理者 API を実行できる

```http
GET /v1/admin/users
```

**レスポンス**

- ステータス：`200 OK`（3-2 では 403 だった）

**ポイント**

- 管理者 API 自体は安全版（`require_admin`）だが、DB の `role` が書き換えられたので、正当な管理者として通ってしまう。
- 3-2 の BFLA 対策をしていても、ロールを書き換えられる入口が 1 つあれば突破される。認可の対策と入力の対策は、両方そろって初めて意味がある。

確認したら `make reset` で `role` を元に戻す。

## 6. 正規表現の入力のエスケープ（ReDoS 対策）

```http
GET /v1/products?q=(a%2B)%2B$&limit=3
```

URL エンコードを外すと `q=(a+)+$` になる。

**レスポンス**

- ステータス：`200 OK`
- ボディ：`data` は空（商品名に `(a+)+$` という文字列を含む商品はない）。応答はすぐに返る。

**ポイント**

- `(a+)+$` は、入れ子になった量指定子のせいで、一致しない入力に対して処理時間が指数関数的に増える正規表現の典型例（壊滅的なバックトラッキング）。ユーザー入力をそのまま正規表現として DB やアプリで評価すると、1 回のリクエストで CPU を占有される（ReDoS）。
- このデモでは、入力を `re.escape` でエスケープし、記号を含めて文字列としての部分一致にしている。そのため `(a+)+$` も単なる文字列として検索され、すぐに結果が返る。
- 入力の最大長（`max_length=50`）を制限していることも対策の一つ。

## ターミナルでのデモ：プロトタイプ汚染

```bash
npm run pollution
```

```text
✖ 危険なマージ
  ({}).isAdmin = true  ← 全オブジェクトが管理者扱いになった

✔ 安全なマージ（危険キーの除外 + Object.create(null)）
  ({}).isAdmin = undefined / settings = {"theme":"dark"}

✔ 基本プロトタイプの凍結（Object.freeze(Object.prototype)）
  ({}).isAdmin = undefined  ← 書き込みが無視される
```

**ポイント**

- 攻撃者が送る JSON は `{"theme": "dark", "__proto__": {"isAdmin": true}}` 。オブジェクトを再帰的にマージする関数がキーを検査せずに代入すると、`__proto__` を通じて `Object.prototype` に `isAdmin: true` が書き込まれる。
- `Object.prototype` はすべてのオブジェクトの共通の親なので、無関係な `{}` まで `isAdmin` が `true` になる。認可の判定に `user.isAdmin` を使っていれば、全員が管理者扱いになる。
- 対策は、`__proto__`、`constructor`、`prototype` のキーを拒否すること、プロトタイプを持たないオブジェクト（`Object.create(null)`）や `Map` を使うこと、`Object.freeze(Object.prototype)` で基本プロトタイプを凍結すること、の組み合わせ。
- Node.js で API を書く場合や、lodash の古いバージョンの `merge` などを使う場合に注意が必要になる。スキーマ検証（`additionalProperties: false`）で未知のキーを拒否しておけば、多くの場合は入口で防げる。

## 片付け

```bash
make reset
```

alice の `role` と `display_name` が元に戻る。
