#!/usr/bin/env bash
# 4-1 HTTP/3 の確認：Caddy（:8443）経由でアクセスし、Alt-Svc による昇格を確認する
set -uo pipefail
echo "== 1回目（TCP / HTTP/2）: Alt-Svc ヘッダーで HTTP/3 を広告する"
curl -sk --http2 -o /dev/null -D - https://localhost:8443/healthz | grep -iE '^(HTTP|alt-svc)'
echo
echo "== HTTP/3 で直接アクセス（HTTP/3 対応 curl をコンテナで実行）"
docker run --rm --network host alpine/curl-http3 \
  curl -sk --http3-only -o /dev/null -w 'http_version=%{http_version} time_connect=%{time_connect}s\n' \
  https://localhost:8443/healthz \
  || echo "HTTP/3 対応 curl を実行できませんでした（UDP 8443 がブロックされている可能性）"
