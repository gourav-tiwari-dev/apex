// Apex's AI door and open-beta inbox (product, 30 Sep - 1 Oct 2026).
//
// AI: a tester's Apex never holds the provider key. It sends its own tester token here; the
// Worker checks it and forwards the request to aicredits.in with the real key (a Cloudflare
// secret). What it refuses, so a leaked token can't run up the bill:
//   - a token that is neither on the list (TESTER_TOKENS) nor registered (TESTERS store)
//   - any model but Apex's two, more than MAX_TOKENS_OUT of answer
//   - more than PER_MINUTE requests a minute from one token (per Worker instance: best effort)
// The provider balance is prepaid: it is the hard cap on what the beta can cost.
//
// Beta:
//   POST /v1/register             a new install gets its own token (at most REGISTER_PER_DAY a day)
//   POST /v1/feedback             a race's rating, comment and summary (JSON, <= 256 KB)
//   POST /v1/feedback/<id>/tape   that race's tape (gzip, <= 25 MB, other drivers' names removed
//                                 by Apex before sending)
// Stores (KV): TESTERS (token -> when registered), FEEDBACK (feedback and tapes).

const PROVIDER = "https://aicredits.in/v1/chat/completions";
const MODELS = ["deepseek-v4-flash", "deepseek-v4.1-flash"];
const MAX_TOKENS_OUT = 1200;
const PER_MINUTE = 30;
const REGISTER_PER_DAY = 200;
const FEEDBACK_MAX = 256 * 1024;
const TAPE_MAX = 25 * 1024 * 1024; // the KV limit for one value

const recent = new Map(); // token -> times of its last requests (this instance only)

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function refuse(status, message) {
  return json(status, { error: { message } });
}

function tooMany(token, now) {
  const times = (recent.get(token) || []).filter((t) => now - t < 60_000);
  times.push(now);
  recent.set(token, times);
  return times.length > PER_MINUTE;
}

function tokenOf(request) {
  return (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "").trim();
}

async function isTester(token, env) {
  if (!token) return false;
  const listed = (env.TESTER_TOKENS || "").split(",").map((t) => t.trim()).filter(Boolean);
  if (listed.includes(token)) return true;
  return env.TESTERS ? (await env.TESTERS.get(token)) !== null : false;
}

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return "apx_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function register(env, now) {
  const day = new Date(now).toISOString().slice(0, 10);
  const counted = Number((await env.TESTERS.get(`count:${day}`)) || 0);
  if (counted >= REGISTER_PER_DAY) {
    return refuse(429, "the beta is full for today, try tomorrow");
  }
  const token = randomToken();
  await env.TESTERS.put(token, JSON.stringify({ registered: new Date(now).toISOString() }));
  await env.TESTERS.put(`count:${day}`, String(counted + 1), { expirationTtl: 3 * 86400 });
  return json(200, { token });
}

async function feedback(request, env, token, now) {
  const text = await request.text();
  if (text.length > FEEDBACK_MAX) return refuse(413, "feedback too big");
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return refuse(400, "the feedback isn't JSON");
  }
  const id = `${new Date(now).toISOString()}_${token.slice(-6)}`;
  await env.FEEDBACK.put(`feedback:${id}`, JSON.stringify({ ...body, tester: token.slice(-6) }));
  return json(200, { id });
}

async function tape(request, env, id) {
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength > TAPE_MAX) return refuse(413, "tape over 25 MB");
  if ((await env.FEEDBACK.get(`feedback:${id}`)) === null) return refuse(404, "no such feedback");
  await env.FEEDBACK.put(`tape:${id}`, bytes);
  return json(200, { id, bytes: bytes.byteLength });
}

async function ask(request, env, token, now) {
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

export async function handle(request, env, now = Date.now()) {
  const path = new URL(request.url).pathname;
  if (request.method !== "POST") return refuse(404, "not here");
  if (path === "/v1/register") return register(env, now);
  const token = tokenOf(request);
  if (!(await isTester(token, env))) return refuse(401, "unknown tester token");
  if (path === "/v1/chat/completions") return ask(request, env, token, now);
  if (path === "/v1/feedback") return feedback(request, env, token, now);
  const tapePath = path.match(/^\/v1\/feedback\/([^/]+)\/tape$/);
  if (tapePath) return tape(request, env, decodeURIComponent(tapePath[1]));
  return refuse(404, "not here");
}

export default { fetch: (request, env) => handle(request, env) };
