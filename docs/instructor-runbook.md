# 講師用ランブック

オンライン研修で「デモを見せるだけ」にするための準備と当日の手順。

## 前日まで

- [ ] 研修で使うモード（ローカル / クラウド）の `.env` を用意し、`make bootstrap`（クラウドは `make infra-up http-env db-init seed`）を完了させる
- [ ] `make run` → `make doctor` で全項目 ✔ を確認する
- [ ] 全デモを一度通しで実行し、各節の画面を録画しておく（障害時の差し替え用）
- [ ] 予備環境として Codespace を 1 つ起動し、同じく `make doctor` まで通しておく
- [ ] クラウドモードの場合：Atlas の Network Access に自宅・会場の IP を許可する。Supabase の無料プロジェクトは一定期間アクセスがないと一時停止されるため、前日にアクセスしておく
- [ ] Docker イメージを事前に取得しておく（当日のダウンロード待ちを避ける）

```bash
docker pull grafana/k6 && docker pull tufin/oasdiff && docker pull alpine/curl-http3
```

## 当日（開始 30 分前）

```bash
make infra-up && npx supabase start -x studio,imgproxy,storage-api,realtime,edge-runtime,logflare,vector,postgres-meta,postgrest
make run          # ターミナル 1
make relay        # ターミナル 2（4-2 で使う）
make reset && make doctor
```

- 画面共有前に VS Code のフォントサイズを確認する（`.vscode/settings.json` で 16px に設定済み）
- ブラウザで `http://localhost:8000/demo/`、`http://localhost:8000/docs`、Grafana（`http://localhost:3000`）をタブで開いておく
- 通知をオフにし、`.env` やトークンが映り込まないようにエディタのタブを整理する

## 節ごとの段取り

| 節 | 事前準備 | 見せるポイント |
|---|---|---|
| 1-1 | ブラウザを 2 タブ開く | REST は最初の表示まで約 2.5 秒、SSE は即座に表示が始まる。WebSocket は他タブにも届く |
| 2-2 | なし | v1 のレスポンスヘッダー（Deprecation / Sunset / Link）。`make brownout-on` で 503 に変わる |
| 2-3 | なし | 深いページのオフセットは遅い。`make explain-pg` で走査ページ数の差（数千 vs 数ページ）を見せる |
| 3-2 / 3-4 | `make reset` | 脆弱版 → 安全版の順で同じ操作をする。3-4 の昇格後は必ず `make reset` |
| 3-3 | なし | `npm run ratelimit` で 429 と RateLimit ヘッダーの `r`（残り）の変化 |
| 4-2 | `make relay` を起動しておく | `npm run idempotency` で注文が 1 件しか作られないこと。`make relay-redeliver` で重複配信がスキップされるログ |
| 4-4 | ターミナルを 2 分割 | 左で `npm run resilience`、右で `make chaos-latency` → `chaos-down` → `chaos-reset`。サーキットの open → half-open → closed |
| 5-3 | 4-4 の直後に実施 | Grafana の Tempo で遅延したトレースを開き、Toxiproxy 区間の遅延と trace_id 付きのログを見せる |

## 障害時の切り替え

| 症状 | 対処 |
|---|---|
| Supabase / Atlas に接続できない | ローカルモードの `.env` に差し替えて `make run` を再起動（事前に両方の `.env` を用意しておく） |
| デモが想定と違う状態になった | `make reset` |
| 環境自体が壊れた | 予備の Codespace に切り替える。それも駄目なら録画を再生する |
| 4-4 で下流が戻らない | `make chaos-reset` |
