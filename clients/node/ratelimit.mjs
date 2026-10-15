// 3-3 レートリミット：20 リクエストを一気に送り、429 と RateLimit ヘッダーの変化を見る
//   npm run ratelimit
import { API, color, now, sleep } from "./lib.mjs";

const hit = async (i) => {
  const r = await fetch(`${API}/v1/products?limit=1&fields=sku`);
  const c = r.status === 200 ? color.ok : color.err;
  console.log(
    `${now()} #${String(i).padStart(2)} ${c(r.status)}  RateLimit: ${r.headers.get("ratelimit")}` +
      (r.status === 429 ? `  Retry-After: ${r.headers.get("retry-after")}` : ""),
  );
};

console.log("① バースト：容量 10 を使い切ると 429");
for (let i = 1; i <= 15; i++) await hit(i);

console.log("\n② 3 秒待つとトークンが補充される（毎秒 2）");
await sleep(3000);
for (let i = 16; i <= 22; i++) await hit(i);
