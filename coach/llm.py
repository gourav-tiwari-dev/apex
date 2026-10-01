"""The one door to the language model: the provider, the model names, the client, and what
it costs (Budget).

aicredits.in serves DeepSeek through an OpenAI-compatible API. Two ways in (30 Sep 2026):
  - a developer's machine: AICREDITS_API_KEY in .env, straight to the provider (as before);
  - an early-access driver's machine: server.json holds the Apex AI door's address and the
    driver's own tester token; the door (server/worker.js) holds the provider key, so the
    installer never ships it.
Until 27 Sep 2026 the coach, the persona and the debrief each built this client themselves,
and the debrief's model name sat in its own code."""

import json
import os
import sys

PROVIDER_URL = "https://aicredits.in/v1"
LIVE_MODEL = "deepseek-v4-flash"  # during a session: the push-to-talk coach
DEBRIEF_MODEL = "deepseek-v4.1-flash"  # after the race: the debrief
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):
    # the installed app: the code is packed inside, a tester's server.json sits next to Apex.exe
    PROJECT = os.path.dirname(sys.executable)
# .env sits in the project folder, one up from coach/
ENV_FILE = os.path.join(PROJECT, ".env")
SERVER_FILE = os.path.join(PROJECT, "server.json")


def where_to_ask(env_file=ENV_FILE, server_file=SERVER_FILE):
    """(base_url, key): the provider with the developer's key, else the Apex AI door with the
    driver's tester token. Raises with a plain reason when there is neither."""
    from dotenv import load_dotenv

    load_dotenv(env_file)
    key = os.environ.get("AICREDITS_API_KEY")
    if key:
        return PROVIDER_URL, key
    if os.path.exists(server_file):
        with open(server_file, encoding="utf-8") as f:
            door = json.load(f)
        return door["url"].rstrip("/") + "/v1", door["token"]
    raise RuntimeError(
        "no AI access: neither .env nor server.json - the radio runs, the coach can't answer"
    )


def open_client(timeout):
    """A client for the provider. Each caller sets how long it can wait for an answer."""
    from openai import OpenAI

    base_url, key = where_to_ask()
    return OpenAI(
        base_url=base_url,
        api_key=key,
        timeout=timeout,
        max_retries=0,
    )


class Budget:
    """What the model has cost this session. There is no cap: push-to-talk never stops for money
    (his call, 25 Sep), and since 1 Oct nothing else on the radio uses the model."""

    # GUESSED prices: derived from the 23 Sep balance drop (Rs 0.62 for 3884 tokens in and
    # 3496 out), assuming output costs 4x input as on aicredits' V3 price list.
    RS_PER_MILLION_IN = 35.0
    RS_PER_MILLION_OUT = 139.0

    def __init__(self):
        self.spent_rs = 0.0

    def cost_of(self, tokens_in, tokens_out):
        return (
            tokens_in * self.RS_PER_MILLION_IN + tokens_out * self.RS_PER_MILLION_OUT
        ) / 1_000_000

    def charge(self, tokens_in, tokens_out):
        cost = self.cost_of(tokens_in, tokens_out)
        self.spent_rs += cost
        return cost
