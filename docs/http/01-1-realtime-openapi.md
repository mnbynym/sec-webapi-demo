# 01-realtime-openapi.http の解説

テキスト 1-1「REST / SSE / WebSocket の使い分け」と 1-3「OpenAPI」に対応する。同じ「生成 AI 風の回答文」を REST と SSE で受け取り、応答の返り方の違いを見せる。最後に FastAPI が生成する OpenAPI 定義を確認する。

このファイルのエンドポイントは DB を使わないので、`make reset` は不要。

REST Client は SSE でもレスポンスの完了を待ってから表示するため、トークンが逐次届く様子は見せられない。逐次表示と WebSocket は、ブラウザで `http://localhost:8000/demo/` を開いて見せる（後述）。

## 1. REST：生成完了後に一括で返す

```http
GET /v1/demo/rest/completion?prompt=方式選定のコツ
```

**レスポンス**

- 約 2.5 秒待ってから `200 OK` が返る（42 トークン × 60 ミリ秒）。
- ボディ：`{"prompt": ..., "text": "WebAPI の方式選定では…", "tokens": 42}`

**説明のポイント**

- リクエストとレスポンスが 1 対 1 で、サーバーは結果が揃うまで何も返せない。
- 生成 AI のように処理に時間がかかる API では、利用者は最初の 1 文字が出るまで待たされる。体感速度の指標である TTFT（最初のトークンが届くまでの時間）が、全体の処理時間と同じになる。

実装は `app/routers/realtime.py` の `rest_completion` にある。

## 2. SSE：トークン単位のストリーミング

```http
GET /v1/demo/sse/completion?prompt=方式選定のコツ
Accept: text/event-stream
```

**レスポンス**

- ヘッダー：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`
- ボディ（イベントの並び）：

```text
retry: 3000

id: 0
event: token
data: {"text": "Web"}

id: 1
event: token
data: {"text": "API"}
…
event: done
data: {"tokens": 42}
```

**説明のポイント**

- 1 本の HTTP レスポンスを開いたまま、サーバーからイベントを順に送り続ける単方向の通信だ。普通の HTTP なので、プロキシやロードバランサーをそのまま通る。
- `id:` を付けておくと、切断時にブラウザの `EventSource` が `Last-Event-ID` ヘッダー付きで自動的に再接続し、続きから受け取れる。`retry:` は再接続までの待ち時間（ミリ秒）の指定だ。
- `X-Accel-Buffering: no` は、nginx などのリバースプロキシにバッファリングさせないための指定だ。バッファリングされると、イベントがまとめて届いてストリーミングの意味がなくなる。
- 生成 AI の API（Claude、OpenAI など）の多くが、ストリーミング応答に SSE を採用している。

## 3. SSE：切断後の再接続（Last-Event-ID）

```http
GET /v1/demo/sse/completion
Last-Event-ID: 40
```

**レスポンス**

- `id: 41` のイベント 1 件と、`event: done` だけが返る。

**説明のポイント**

- サーバーは `Last-Event-ID` を見て、受信済みのイベントを飛ばして続きから送る。ブラウザの `EventSource` が自動で行う再接続を、手動で再現したものだ。
- 続きから再開するには、サーバー側がイベント ID と内容の対応を保持している必要がある（このデモでは回答文が固定なので計算で求めている）。

## 4. OpenAPI 定義（1-3）

```http
GET /openapi.json
```

**レスポンス**

- 先頭が `"openapi": "3.1.0"` の JSON。

**説明のポイント**

- FastAPI は、Pydantic のモデルと型ヒントから OpenAPI 3.1 の定義を自動生成する（コードファースト）。`http://localhost:8000/docs` で Swagger UI として見られる。
- 3.1 は JSON Schema と完全に互換になった。たとえば `note: str | None` は、`anyOf: [{"type": "string"}, {"type": "null"}]` として表現される（3.0 の `nullable: true` は使われない）。
- エラーレスポンスは、すべて `application/problem+json`（`ProblemDetails`）として定義している。FastAPI が自動で付ける 422 の定義も差し替えている（`app/main.py` の `custom_openapi`）。
- この定義が、1-3 の Spectral によるリント（`make lint-openapi`）、Prism によるモック（`make mock`）、5-1 の Schemathesis や oasdiff の入力になる。

## ブラウザでの比較（/demo/）

VS Code の「ポート」タブから 8000 番を開き、`/demo/` に移動する。

| 列 | 操作 | 見せるポイント |
|---|---|---|
| REST | 「回答を取得」 | 約 2.5 秒後に全文が表示される。「最初の表示」と「完了」が同じ時間になる |
| SSE | 「ストリーミング開始」 | すぐに 1 文字目が表示され、少しずつ増える。「最初の表示」が数十ミリ秒になる |
| SSE | 「途中で切断して再接続」 | 「〔切断〕」の後、最後に受け取った id の続きから再開する |
| WebSocket | 表示名を変えて「接続」し、メッセージを送る | ブラウザのタブを 2 つ開くと、送ったメッセージが両方に届く（双方向・ブロードキャスト） |

WebSocket は、1 本の接続上でクライアントからもサーバーからも任意のタイミングで送れる。チャットや共同編集のように双方向の通信が必要な場合に使う。一方で、通常の HTTP とは別のプロトコルになるため、ロードバランサーの設定、接続数の管理、再接続の実装がクライアント側の責任になる。このデモのチャットは接続をプロセスのメモリで管理しているので、API を複数台に増やすと別のサーバーに接続したユーザーには届かない（Redis Pub/Sub などが必要になる）。
