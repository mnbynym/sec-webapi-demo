// 4-2 Idempotency-Key：同じキーで同時に 5 リクエストを送る（二重クリック・自動リトライの再現）
//   npm run idempotency
import { API, color, login } from "./lib.mjs";

const token = await login("alice");
const key = `demo-${crypto.randomUUID()}`; // クライアントがキーを生成する
const body = JSON.stringify({ items: [{ sku: "SKU-000030", quantity: 1 }] });

const send = (payload = body) =>
  fetch(`${API}/v1/orders`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": key },
    body: payload,
  }).then(async (r) => ({ status: r.status, replayed: r.headers.get("idempotent-replayed"), body: await r.json() }));

const show = (label, r) => {
  const c = r.status < 300 ? color.ok : r.status === 409 ? color.warn : color.err;
  console.log(`${label.padEnd(24)} ${c(r.status)} ${r.replayed ? color.dim("(replayed) ") : ""}${r.body.id ?? r.body.title}`);
};

console.log(`Idempotency-Key: ${key}\n`);
console.log("① 同時に 5 リクエスト（1 件だけ処理され、残りは処理中の 409）");
const results = await Promise.all(Array.from({ length: 5 }, () => send()));
results.forEach((r, i) => show(`  request #${i + 1}`, r));

console.log("\n② 完了後に同じキーで再送（保存済みレスポンスを再生、同じ注文 ID）");
show("  retry", await send());

console.log("\n③ 同じキーで内容を変更（改ざんとみなして 422）");
show("  tampered", await send(JSON.stringify({ items: [{ sku: "SKU-000030", quantity: 50 }] })));

const ids = new Set([...results, await send()].filter((r) => r.body.id).map((r) => r.body.id));
console.log(`\n作成された注文: ${ids.size} 件 ${[...ids].join(", ")}`);
