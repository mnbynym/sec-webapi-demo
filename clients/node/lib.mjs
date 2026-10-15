// Node クライアント共通処理：.env の読み込み、Supabase Auth ログイン、表示ヘルパー
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
try {
  process.loadEnvFile(`${root}.env`);
} catch {
  console.error(".env がありません（make env-sync を実行）");
  process.exit(1);
}

export const API = process.env.API_BASE_URL ?? "http://localhost:8000";
const SB = process.env.SUPABASE_URL;
const ANON = process.env.SUPABASE_ANON_KEY;

export async function login(user = "alice") {
  const r = await fetch(`${SB}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: ANON, "Content-Type": "application/json" },
    body: JSON.stringify({ email: `${user}@example.com`, password: process.env.DEMO_PASSWORD }),
  });
  if (!r.ok) throw new Error(`login failed: ${r.status} ${await r.text()}`);
  return (await r.json()).access_token;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const now = () => new Date().toISOString().slice(11, 23);
export const color = {
  ok: (s) => `\x1b[32m${s}\x1b[0m`,
  warn: (s) => `\x1b[33m${s}\x1b[0m`,
  err: (s) => `\x1b[31m${s}\x1b[0m`,
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
};
