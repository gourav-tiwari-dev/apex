# Apex AI door

Early-access drivers' Apex asks the model through this Cloudflare Worker, so the provider key
never ships in the installer. What it refuses and why: see the top of `worker.js`.
Tests: `node server/worker.test.mjs`.

## One-time setup (Gourav)
1. Make a free Cloudflare account at https://dash.cloudflare.com/sign-up
2. From `apex_telemetry/server`: `npx wrangler login` (opens the browser once)
3. `npx wrangler secret put AICREDITS_API_KEY`, then paste the key from `.env`
4. `npx wrangler kv namespace create TESTERS` and `npx wrangler kv namespace create FEEDBACK`, then
   put the two printed ids into wrangler.toml
5. `npx wrangler deploy`. It prints the door's address, `https://apex-ai-door.<you>.workers.dev`
6. Beta build: `python packaging/build.py --out BUILD --door https://apex-ai-door.<you>.workers.dev`,
   then `python packaging/make_installer.py BUILD`. Each install registers its own token.

## Reading the beta
`python server/inbox.py --replay`: feedback newest first, downloads tapes and replays them.

## Each new tester
1. `python server/new_tester.py their@email`. It prints their token and the new TESTER_TOKENS
2. `npx wrangler secret put TESTER_TOKENS`, then paste the printed list
3. Their `server.json` (next to apex.exe): `{"url": "https://apex-ai-door.<you>.workers.dev", "token": "apx_..."}`

To take access back: delete their line in `testers.json`, run step 2 again with the shorter list.

## Cost
- Workers free plan: 100,000 requests a day, then requests fail (no bill).
- The model: every tester's answers are paid from the AICredits balance. Apex caps a session
  at Rs 10 on the driver's side; the door caps each answer at 1,200 tokens and a token at
  30 questions a minute.
