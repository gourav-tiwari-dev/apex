// Apex's AI door for early-access drivers (product, 30 Sep 2026).
//
// A tester's Apex never holds the provider key: it sends its own tester token here, this Worker
// checks it and forwards the request to aicredits.in with the real key (a Cloudflare secret).
// What it refuses, so a leaked token can't run up the bill:
//   - a token that isn't on the list (TESTER_TOKENS secret, comma separated)
//   - any model but Apex's two
//   - more than MAX_TOKENS_OUT of answer
//   - more than PER_MINUTE requests a minute from one token (per Worker instance: best effort;
//     the provider balance is the hard cap)
// Only POST /v1/chat/completions exists. Deployed with `npx wrangler deploy` (see README.md).

const PROVIDER = "https://aicredits.in/v1/chat/completions";
const MODELS = ["deepseek-v4-flash", "deepseek-v4.1-flash"];
const MAX_TOKENS_OUT = 1200;
const PER_MINUTE = 30;

const recent = new Map(); // token -> times of its last requests (this instance only)

function refuse(status, message) {
  return new Response(JSON.stringify({ error: { message } }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function tooMany(token, now) {
  const times = (recent.get(token) || []).filter((t) => now - t < 60_000);
  times.push(now);
  recent.set(token, times);
  return times.length > PER_MINUTE;
}

export async function handle(request, env, now = Date.now()) {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== "/v1/chat/completions") {
    return refuse(404, "not here");
  }
  const token = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
  const allowed = (env.TESTER_TOKENS || "").split(",").map((t) => t.trim()).filter(Boolean);
  if (!token || !allowed.includes(token)) {
    return refuse(401, "unknown tester token");
  }
  if (tooMany(token, now)) {
    return refuse(429, "slow down: too many questions this minute");
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return refuse(400, "the request isn't JSON");
  }
  if (!MODELS.includes(body.model)) {
    return refuse(400, "model not allowed");
  }
  body.stream = false;
  body.max_tokens = Math.min(Number(body.max_tokens) || MAX_TOKENS_OUT, MAX_TOKENS_OUT);
  const answer = await fetch(PROVIDER, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.AICREDITS_API_KEY}`,
    },
    body: JSON.stringify(body),
  });
  return new Response(answer.body, {
    status: answer.status,
    headers: { "content-type": answer.headers.get("content-type") || "application/json" },
  });
}

export default { fetch: (request, env) => handle(request, env) };
