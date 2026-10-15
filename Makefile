SHELL := /bin/bash
.DEFAULT_GOAL := help
PY := uv run python
API := http://localhost:8000
# Supabase CLI で起動しないサービス（DB と Auth と API Gateway だけを使う）
SUPABASE_EXCLUDE ?= studio,imgproxy,storage-api,realtime,edge-runtime,logflare,vector,postgres-meta,postgrest

help: ## コマンド一覧
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

# ------------------------------------------------------------------ 環境構築
bootstrap: setup infra-up supabase-start env-sync db-init seed ## ローカルモードの環境を一括構築
	@echo -e "\n完了。別ターミナルで make run、その後 make doctor"

setup: ## 依存関係のインストール（Python / Node）
	./scripts/setup.sh

infra-up: ## Docker Compose（Mongo / Redis / Toxiproxy / otel-lgtm / Caddy）を起動
	docker compose up -d --wait mongo redis
	docker compose up -d toxiproxy otel-lgtm caddy

supabase-start: ## ローカル Supabase（PostgreSQL + Auth）を起動
	npx supabase start -x $(SUPABASE_EXCLUDE)

env-sync: ## ローカル Supabase の接続情報を .env と clients/http/.env に書き込む
	$(PY) -m scripts.sync_env --supabase

http-env: ## clients/http/.env だけを再生成（クラウドモード用）
	$(PY) -m scripts.sync_env

db-init: ## スキーマとインデックスを作成
	$(PY) -m scripts.db_init

seed: ## デモデータを投入（注文 100 万件・商品 10 万件）
	$(PY) -m scripts.seed

seed-small: ## 少量のデモデータを投入（動作確認用）
	$(PY) -m scripts.seed --small

reset: ## デモで変化した状態を初期化（各デモの前に実行）
	$(PY) -m scripts.reset

doctor: ## 研修前の事前チェック
	$(PY) -m scripts.doctor

down: ## すべて停止
	docker compose down
	npx supabase stop

# ------------------------------------------------------------------ 実行
run: ## API サーバーを起動（Toxiproxy から到達できるよう 0.0.0.0 で待ち受け）
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

relay: ## 4-2 outbox リレーを常駐起動
	$(PY) -m app.workers.outbox_relay

relay-redeliver: ## 4-2 配信済みイベントを重複配信して冪等コンシューマを確認
	$(PY) -m app.workers.outbox_relay --redeliver 5

mcp: ## 1-2 MCP サーバーを stdio で起動（通常は .mcp.json 経由で Claude Code が起動する）
	$(PY) -m app.mcp_server

# ------------------------------------------------------------------ 章ごとのデモ
tools: ## 1-2 OpenAPI → Tool Use 定義に変換
	$(PY) -m scripts.openapi_to_tools --tag products

lint-openapi: openapi-export ## 1-3 Spectral で OpenAPI をリント
	npx spectral lint contracts/openapi.json

mock: ## 1-3 Prism でモックサーバーを起動（:4010）
	npx prism mock contracts/openapi.baseline.json -p 4010

explain-pg: ## 2-3 オフセット vs カーソルの実行計画（PostgreSQL）
	$(PY) -m scripts.explain_pg

explain-mongo: ## 2-3 ESR インデックスの効果（MongoDB）
	$(PY) -m scripts.explain_mongo

brownout-on: ## 2-2 GET /v1/orders/{id} を計画停止
	docker compose exec redis redis-cli set brownout:v1-get-order 1 EX 600

brownout-off: ## 2-2 計画停止を解除
	docker compose exec redis redis-cli del brownout:v1-get-order

k6-ratelimit: ## 3-3 k6 でレートリミットに負荷をかける
	docker run --rm -i --network host -e BASE_URL=$(API) grafana/k6 run - < clients/k6/ratelimit.js

h3-check: ## 4-1 HTTP/3（Caddy）の確認
	./scripts/h3_check.sh

chaos-latency: ## 4-4 下流に 3 秒の遅延を注入
	./scripts/chaos.sh latency 3000

chaos-slow: ## 4-4 下流を不安定な遅延に
	./scripts/chaos.sh slow

chaos-down: ## 4-4 下流を停止
	./scripts/chaos.sh down

chaos-reset: ## 4-4 障害注入を解除
	./scripts/chaos.sh reset

openapi-export: ## 現在の OpenAPI を contracts/openapi.json に書き出す
	$(PY) -m scripts.export_openapi

openapi-baseline: ## 5-1 契約のベースラインを更新（破壊的変更の比較用定義も再生成）
	$(PY) -m scripts.export_openapi --baseline --breaking-example

oasdiff: ## 5-1 破壊的変更の検出（ベースライン vs 破壊的変更の例）
	docker run --rm -v $(CURDIR)/contracts:/c tufin/oasdiff breaking /c/openapi.baseline.json /c/openapi.breaking-example.json --fail-on ERR

oasdiff-current: openapi-export ## 5-1 ベースライン vs 現在の実装
	docker run --rm -v $(CURDIR)/contracts:/c tufin/oasdiff breaking /c/openapi.baseline.json /c/openapi.json --fail-on ERR

fuzz: ## 5-1 Schemathesis でプロパティベースドテスト（API 起動が必要）
	./scripts/fuzz.sh

lint: ## Python のリント
	uv run ruff check . && uv run ruff format --check .

.PHONY: $(shell grep -oE '^[a-zA-Z0-9_-]+:' $(MAKEFILE_LIST) | tr -d :)
