#!/usr/bin/env bash
# 4-4 Toxiproxy による障害注入
#   scripts/chaos.sh latency [ms]   下流に遅延を注入（既定 3000ms）
#   scripts/chaos.sh slow           下流を 800ms ± 400ms の不安定な遅延に
#   scripts/chaos.sh down           下流を停止（接続拒否）
#   scripts/chaos.sh up             下流を復旧
#   scripts/chaos.sh reset          すべての障害を解除
#   scripts/chaos.sh status         現在の状態を表示
set -euo pipefail
API=${TOXIPROXY_API:-http://localhost:8474}
P=inventory

toxic() { curl -fsS -X POST "$API/proxies/$P/toxics" -H 'Content-Type: application/json' -d "$1" >/dev/null; }
clear() { for t in $(curl -fsS "$API/proxies/$P/toxics" | python3 -c 'import sys,json;[print(t["name"]) for t in json.load(sys.stdin)]'); do
            curl -fsS -X DELETE "$API/proxies/$P/toxics/$t" >/dev/null; done; }

case "${1:-status}" in
  latency) clear; toxic "{\"name\":\"latency\",\"type\":\"latency\",\"stream\":\"downstream\",\"attributes\":{\"latency\":${2:-3000},\"jitter\":0}}"
           echo "latency ${2:-3000}ms を注入" ;;
  slow)    clear; toxic '{"name":"latency","type":"latency","stream":"downstream","attributes":{"latency":800,"jitter":400}}'
           echo "800ms ± 400ms の遅延を注入" ;;
  down)    curl -fsS -X POST "$API/proxies/$P" -H 'Content-Type: application/json' -d '{"enabled":false}' >/dev/null
           echo "下流を停止" ;;
  up)      curl -fsS -X POST "$API/proxies/$P" -H 'Content-Type: application/json' -d '{"enabled":true}' >/dev/null
           echo "下流を復旧" ;;
  reset)   curl -fsS -X POST "$API/reset" >/dev/null; echo "障害注入を解除" ;;
  status)  curl -fsS "$API/proxies/$P" | python3 -m json.tool ;;
  *) echo "usage: $0 {latency [ms]|slow|down|up|reset|status}"; exit 1 ;;
esac
