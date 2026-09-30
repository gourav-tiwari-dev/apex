// node server/worker.test.mjs - the Worker's refusals and its forwarding, with the provider faked.
import assert from "node:assert/strict";
import { handle } from "./worker.js";

const env = { TESTER_TOKENS: "tok_sam, tok_ana", AICREDITS_API_KEY: "sk-real" };
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url, init, body: JSON.parse(init.body) };
  return new Response(JSON.stringify({ choices: [{ message: { content: "Fuel's fine." } }] }), {
    status: 200, headers: { "content-type": "application/json" },
  });
};

const ask = (token, body, path = "/v1/chat/completions", method = "POST") =>
  new Request(`https://apex.example${path}`, {
    method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
const good = { model: "deepseek-v4-flash", messages: [{ role: "user", content: "fuel?" }], max_tokens: 99999, stream: true };

// an unknown token never reaches the provider
sent = null;
assert.equal((await handle(ask("tok_stolen", good), env)).status, 401);
assert.equal(sent, null);

// a known token is forwarded with the REAL key, answer length capped, streaming off
const res = await handle(ask("tok_sam", good), env);
assert.equal(res.status, 200);
assert.equal(sent.url, "https://aicredits.in/v1/chat/completions");
assert.equal(sent.init.headers.authorization, "Bearer sk-real");
assert.equal(sent.body.max_tokens, 1200);
assert.equal(sent.body.stream, false);
assert.equal((await res.json()).choices[0].message.content, "Fuel's fine.");

// only Apex's models, only this path
sent = null;
assert.equal((await handle(ask("tok_sam", { ...good, model: "gpt-9-ultra" }), env)).status, 400);
assert.equal((await handle(ask("tok_sam", good, "/v1/models", "GET"), env)).status, 404);
assert.equal(sent, null);

// a flood from one token is cut off; another tester is not affected
const t0 = 1_000_000;
let last;
for (let i = 0; i < 31; i++) last = await handle(ask("tok_ana", good), env, t0 + i);
assert.equal(last.status, 429);
assert.equal((await handle(ask("tok_sam", good), env, t0 + 40)).status, 200);
// a minute later tok_ana may ask again
assert.equal((await handle(ask("tok_ana", good), env, t0 + 61_000)).status, 200);

console.log("worker: all checks passed");
