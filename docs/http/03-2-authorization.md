# 03-2-authorization.http の解説

テキスト 3-2「認可（BOLA / BFLA）」に対応する。OWASP API Security Top 10 の 1 位（BOLA）と 5 位（BFLA）を、意図的に脆弱にしたエンドポイント（`/vuln/*`）と安全なエンドポイント（`/v1/*`、`/v2/*`）で比べる。

| | 意味 | 典型的な原因 |
|---|---|---|
| BOLA（Broken Object Level Authorization） | 他人のデータ（オブジェクト）にアクセスできる | ID だけでデータを取得し、所有者を確認していない |
| BFLA（Broken Function Level Authorization） | 権限のない機能（管理者機能など）を実行できる | ログインしているかだけを確認し、ロールを確認していない |

どちらも「認証（誰か）」はできていて、「認可（その人に許されているか）」が抜けている点が共通している。

`/vuln/*` は `ENABLE_VULN_ENDPOINTS=true` のときだけ有効になる。実装は `app/routers/vuln.py` にあり、各関数のコメントに対応する安全版を書いてある。

## 1. ログイン（alice / bob）

alice が攻撃者、bob が被害者という役割で使う。

## 2. bob が自分の注文を確認する

```http
GET /v1/orders?limit=1
Authorization: Bearer <bob のトークン>
```

bob の最新の注文 1 件が返る。`order_no`（連番）と `id`（UUID）の両方を、後続のリクエストで使う。

## 3. ✖ BOLA（脆弱版）：注文番号だけで照会

```http
GET /vuln/orders/{bob の order_no}
Authorization: Bearer <alice のトークン>
```

**レスポンス**

- ステータス：`200 OK`
- ボディ：bob の注文（`note: "bob-3"` など）がそのまま返る。

**ポイント**

- 脆弱版の SQL は `where order_no = $1` だけで、注文の所有者を確認していない。
- `order_no` は連番なので、攻撃者は自分の注文番号から前後の番号を試すだけで、他人の注文を次々に取得できる。
- alice は正規のトークンを持っているので、認証は通っている。「ログインしていれば OK」では、他人のデータを守れない。

## 4. ✔ 安全版：UUID + 所有者チェック

```http
GET /v2/orders/{bob の id}
Authorization: Bearer <alice のトークン>
```

**レスポンス**

- ステータス：`404 Not Found`

**ポイント**

- 安全版の SQL は `where id = $1 and user_id = $2` で、トークンのユーザー ID を必ず条件に含める。
- 他人の注文は 403 ではなく 404 で返し、存在するかどうかすら知らせない。403 を返すと「その ID の注文は存在する」ことが分かってしまう。
- 外部に公開する ID は推測困難な UUID にしている。ただし UUID は対策の補助にすぎない。ID が漏れれば（URL の共有、ログなど）推測は不要になるので、所有者のチェックが本質。
- 対策の漏れを防ぐには、所有者チェックを各エンドポイントで個別に書くのではなく、データ取得の共通関数（このデモでは `_get_owned`）に集約する。Supabase を使う場合は、DB の行レベルセキュリティ（RLS）で多層的に防ぐこともできる。

## 5. ✖ BFLA（脆弱版）：一般ユーザーが管理者 API を実行

```http
GET /vuln/admin/users
Authorization: Bearer <alice のトークン>
```

**レスポンス**

- ステータス：`200 OK`
- ボディ：全ユーザーのメールアドレスとロールの一覧。

**ポイント**

- 脆弱版は「認証済みかどうか」しか確認していない。
- 管理者向けの URL は画面に表示されていなくても、URL の推測（`/admin/...`）、フロントエンドの JavaScript の解析、API ドキュメントなどから容易に見つかる。画面に出さないことは対策にならない。

## 6. ✔ 安全版：ロールを DB で確認

```http
GET /v1/admin/users
Authorization: Bearer <alice のトークン>
```

**レスポンス**

- ステータス：`403 Forbidden`
- ボディ：`detail: 管理者権限が必要です`

**ポイント**

- 安全版は `Depends(require_admin)` で、DB の `profiles.role` が `admin` かどうかを確認している。
- ロールを JWT のクレームではなく DB で確認しているのは、権限を剥奪したときに即座に反映させるため。JWT に入れたロールは、トークンの有効期限まで古い値のまま残る。
- ここは 404 ではなく 403 を返している。管理者 API の存在自体は秘密ではないので、「権限がない」ことを明示した方が利用者に分かりやすい。

## 7. ✔ 一般ユーザーは商品を削除できない

```http
DELETE /v1/products/SKU-000001
Authorization: Bearer <alice のトークン>
```

- ステータス：`403`

GET（参照）は誰でもできても、POST / PATCH / DELETE（変更）は管理者だけ、のようにメソッドごとに権限が違う場合も BFLA の対象になる。参照の権限チェックだけをして、変更系の確認を忘れる、という漏れが起きやすい。

## 補足

- このデモではデータを変更しないので、`make reset` は不要。
- 管理者への権限昇格（Mass Assignment）は 3-4 で確認する。
