// 3-3 k6 によるレートリミットの負荷テスト（毎秒 10 リクエスト × 20 秒）
//   make k6-ratelimit
// トークンバケット（容量 10、毎秒 2 補充）なので、約 2 割だけ 200、残りは 429 になる
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const limited = new Counter("rate_limited_429");
const allowed = new Counter("allowed_200");

export const options = {
  scenarios: {
    burst: { executor: "constant-arrival-rate", rate: 10, timeUnit: "1s", duration: "20s", preAllocatedVUs: 10 },
  },
  thresholds: { "http_req_duration{status:200}": ["p(95)<500"] },
};

export default function () {
  const r = http.get(`${BASE}/v1/products?limit=1&fields=sku`);
  if (r.status === 429) limited.add(1);
  if (r.status === 200) allowed.add(1);
  check(r, {
    "200 or 429": (x) => x.status === 200 || x.status === 429,
    "429 has Retry-After": (x) => x.status !== 429 || x.headers["Retry-After"] !== undefined,
    "RateLimit header present": (x) => x.headers["Ratelimit"] !== undefined,
  });
}
