// 4-4 クライアント側の回復性：タイムアウト、指数バックオフ + Full Jitter、リトライバジェット、サーキットブレーカー
//   npm run resilience            ... 実行中に別ターミナルで make chaos-latency / chaos-down / chaos-reset
//   npm run resilience -- 60      ... リクエスト回数を指定
import { API, color, now, sleep } from "./lib.mjs";

const TOTAL = Number(process.argv[2] ?? 40);
const CLIENT_TIMEOUT_MS = 2500; // 呼び出し先（API: 1.5 秒）より長くする
const MAX_RETRIES = 3;
const BASE_MS = 200;
const CAP_MS = 2000;
const RETRYABLE = new Set([429, 502, 503, 504]);

class CircuitBreaker {
  state = "closed";
  failures = [];
  openedAt = 0;
  constructor({ windowSize = 10, threshold = 0.5, minCalls = 5, coolDownMs = 5000 } = {}) {
    Object.assign(this, { windowSize, threshold, minCalls, coolDownMs });
  }
  canPass() {
    if (this.state === "open" && Date.now() - this.openedAt >= this.coolDownMs) this.transition("half-open");
    return this.state !== "open";
  }
  record(ok) {
    if (this.state === "half-open") return this.transition(ok ? "closed" : "open");
    this.failures = [...this.failures, !ok].slice(-this.windowSize);
    const rate = this.failures.filter(Boolean).length / this.failures.length;
    if (this.failures.length >= this.minCalls && rate >= this.threshold) this.transition("open");
  }
  transition(next) {
    if (next === this.state) return;
    console.log(color.warn(`  ⚡ circuit ${this.state} → ${next}`));
    this.state = next;
    if (next === "open") this.openedAt = Date.now();
    if (next === "closed") this.failures = [];
  }
}

class RetryBudget {
  // 直近のリクエストに対するリトライの割合を上限（20%）以内に抑え、障害時の負荷増幅を防ぐ
  requests = 0;
  retries = 0;
  constructor(ratio = 0.2, minRetries = 3) {
    Object.assign(this, { ratio, minRetries });
  }
  canRetry() {
    return this.retries < Math.max(this.minRetries, this.requests * this.ratio);
  }
}

const breaker = new CircuitBreaker();
const budget = new RetryBudget();
const fullJitter = (attempt) => Math.random() * Math.min(CAP_MS, BASE_MS * 2 ** attempt);

async function call(sku) {
  budget.requests++;
  for (let attempt = 0; ; attempt++) {
    if (!breaker.canPass()) return { outcome: "short-circuit" };
    const started = Date.now();
    const deadline = started + CLIENT_TIMEOUT_MS;
    let status;
    let retryAfter = 0;
    try {
      const r = await fetch(`${API}/v1/inventory/${sku}`, {
        headers: { "X-Request-Deadline": String(deadline) }, // 残り時間をサーバーに伝播
        signal: AbortSignal.timeout(CLIENT_TIMEOUT_MS),
      });
      status = r.status;
      if (r.ok) {
        breaker.record(true);
        return { outcome: "ok", status, ms: Date.now() - started, attempt };
      }
      breaker.record(false);
      if (!RETRYABLE.has(status)) return { outcome: "fail", status, attempt };
      retryAfter = Number(r.headers.get("retry-after")) * 1000 || 0;
    } catch (e) {
      breaker.record(false);
      status = e.name === "TimeoutError" ? "timeout" : "network";
    }
    if (attempt >= MAX_RETRIES || !budget.canRetry()) return { outcome: "fail", status, attempt };
    budget.retries++;
    const wait = Math.max(fullJitter(attempt), retryAfter);
    console.log(color.dim(`    retry #${attempt + 1} after ${wait.toFixed(0)}ms (${status})`));
    await sleep(wait);
  }
}

console.log(`${TOTAL} リクエスト / timeout ${CLIENT_TIMEOUT_MS}ms / retries ≤ ${MAX_RETRIES} / budget 20%\n`);
for (let i = 1; i <= TOTAL; i++) {
  const r = await call(`SKU-${String(i).padStart(6, "0")}`);
  const label =
    r.outcome === "ok" ? color.ok(`200 ${r.ms}ms`) :
    r.outcome === "short-circuit" ? color.warn("short-circuit（下流へ送らず即時失敗）") :
    color.err(`failed (${r.status})`);
  console.log(`${now()} #${String(i).padStart(2)} [${breaker.state.padEnd(9)}] ${label}`);
  await sleep(500);
}
console.log(`\nリトライ ${budget.retries} 回 / リクエスト ${budget.requests} 回`);
