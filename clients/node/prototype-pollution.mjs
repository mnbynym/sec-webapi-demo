// 3-4 プロトタイプ汚染：危険なディープマージと安全な実装の比較（サーバー不要）
//   npm run pollution
import { color } from "./lib.mjs";

const payload = JSON.parse('{"theme": "dark", "__proto__": {"isAdmin": true}}'); // 攻撃者が送る JSON

function unsafeMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (typeof source[key] === "object" && source[key] !== null) {
      target[key] ??= {};
      unsafeMerge(target[key], source[key]); // key === "__proto__" のとき Object.prototype に書き込む
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

const FORBIDDEN = new Set(["__proto__", "constructor", "prototype"]);
function safeMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (FORBIDDEN.has(key)) continue; // 危険なキーを拒絶
    const v = source[key];
    if (typeof v === "object" && v !== null) {
      target[key] = safeMerge(Object.hasOwn(target, key) ? target[key] : Object.create(null), v);
    } else {
      target[key] = v;
    }
  }
  return target;
}

console.log("✖ 危険なマージ");
unsafeMerge({}, payload);
const victim = {}; // 無関係な新しいオブジェクト
console.log(`  ({}).isAdmin = ${color.err(victim.isAdmin)}  ← 全オブジェクトが管理者扱いになった`);
delete Object.prototype.isAdmin; // 後片付け

console.log("\n✔ 安全なマージ（危険キーの除外 + Object.create(null)）");
const settings = safeMerge(Object.create(null), payload);
console.log(`  ({}).isAdmin = ${color.ok(({}).isAdmin)} / settings = ${JSON.stringify(settings)}`);

console.log("\n✔ 基本プロトタイプの凍結（Object.freeze(Object.prototype)）");
Object.freeze(Object.prototype);
unsafeMerge({}, payload);
console.log(`  ({}).isAdmin = ${color.ok(({}).isAdmin)}  ← 書き込みが無視される`);
