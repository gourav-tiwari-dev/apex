// node server/worker.test.mjs - the Worker's refusals, forwarding and beta inbox, with the
// provider and Cloudflare's KV faked.
import assert from "node:assert/strict";
import { handle } from "./worker.js";

class FakeKV {
  constructor() { this.data = new Map(); }
  async get(key) { return this.data.has(key) ? this.data.get(key) : null; }
  async put(key, value) { this.data.set(key, value); }
}

const env = { TESTER_TOKENS: "tok_sam, tok_ana", AICREDITS_API_KEY: "sk-real", TESTERS: new FakeKV(), FEEDBACK: new FakeKV() };
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url, init, body: JSON.parse(init.body) };
  return new Response(JSON.stringify({ choices: [{ message: { content: "Fuel's fine." } }] }), {
    status: 200, headers: { "content-type": "application/json" },
  });
};

const post = (token, path, body, raw = false) =>
  new Request(`https://apex.example${path}`, {
    method: "POST",
    headers: token ? { authorization: `Bearer ${token}`, "content-type": "application/json" } : {},
    body: raw ? body : JSON.stringify(body),
  });
const good = { model: "deepseek-v4-flash", messages: [{ role: "user", content: "fuel?" }], max_tokens: 99999, stream: true };
const ASK = "/v1/chat/completions";

// ---- the AI door ----
sent = null;
assert.equal((await handle(post("tok_stolen", ASK, good), env)).status, 401);
assert.equal(sent, null);

let res = await handle(post("tok_sam", ASK, good), env);
assert.equal(res.status, 200);
assert.equal(sent.url, "https://aicredits.in/v1/chat/completions");
assert.equal(sent.init.headers.authorization, "Bearer sk-real");
assert.equal(sent.body.max_tokens, 1200);
assert.equal(sent.body.stream, false);
assert.equal((await res.json()).choices[0].message.content, "Fuel's fine.");

sent = null;
assert.equal((await handle(post("tok_sam", ASK, { ...good, model: "gpt-9-ultra" }), env)).status, 400);
assert.equal((await handle(new Request("https://apex.example/v1/models"), env)).status, 404);
assert.equal(sent, null);

const t0 = 1_000_000;
let last;
for (let i = 0; i < 31; i++) last = await handle(post("tok_ana", ASK, good), env, t0 + i);
assert.equal(last.status, 429);
assert.equal((await handle(post("tok_sam", ASK, good), env, t0 + 40)).status, 200);
assert.equal((await handle(post("tok_ana", ASK, good), env, t0 + 61_000)).status, 200);

// ---- the beta: a new install registers, then may ask and send feedback ----
const day = Date.UTC(2026, 9, 1, 12);
res = await handle(post(null, "/v1/register", {}), env, day);
assert.equal(res.status, 200);
const { token } = await res.json();
assert.match(token, /^apx_[0-9a-f]{48}$/);
assert.equal((await handle(post(token, ASK, good), env, day)).status, 200);

res = await handle(post(token, "/v1/feedback", { rating: 4, comment: "spotter late at Eau Rouge", track: "Spa" }), env, day);
assert.equal(res.status, 200);
const { id } = await res.json();
const saved = JSON.parse(await env.FEEDBACK.get(`feedback:${id}`));
assert.equal(saved.rating, 4);
assert.equal(saved.tester, token.slice(-6));

const tapeBytes = new Uint8Array([31, 139, 8, 0, 1, 2, 3]);
res = await handle(post(token, `/v1/feedback/${encodeURIComponent(id)}/tape`, tapeBytes, true), env, day);
assert.equal(res.status, 200);
assert.equal((await env.FEEDBACK.get(`tape:${id}`)).byteLength, 7);

// a tape for feedback that doesn't exist, feedback without a token, oversized feedback
assert.equal((await handle(post(token, "/v1/feedback/nope/tape", tapeBytes, true), env, day)).status, 404);
assert.equal((await handle(post(null, "/v1/feedback", { rating: 5 }), env, day)).status, 401);
assert.equal((await handle(post(token, "/v1/feedback", { c: "x".repeat(300_000) }), env, day)).status, 413);

// registrations stop at the daily cap
env.TESTERS.data.set("count:2026-10-01", "200");
assert.equal((await handle(post(null, "/v1/register", {}), env, day)).status, 429);

console.log("worker: all checks passed");
