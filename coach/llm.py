"""The one door to the language model: the provider, the model names, the client, and what
it costs (Budget).

aicredits.in serves DeepSeek through an OpenAI-compatible API; the key is AICREDITS_API_KEY in
.env. Until 27 Sep 2026 the coach, the persona and the debrief each built this client
themselves, and the debrief's model name sat in its own code."""

import os

PROVIDER_URL = "https://aicredits.in/v1"
LIVE_MODEL = (
    "deepseek-v4-flash"  # during a session: the push-to-talk coach, line phrasing
)
DEBRIEF_MODEL = "deepseek-v4.1-flash"  # after the race: the debrief
# .env sits in the project folder, one up from coach/
ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)


def open_client(timeout):
    """A client for the provider. Each caller sets how long it can wait for an answer."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ENV_FILE)
    return OpenAI(
        base_url=PROVIDER_URL,
        api_key=os.environ["AICREDITS_API_KEY"],
        timeout=timeout,
        max_retries=0,
    )


class Budget:
    """What the LLM has cost this session. Past the cap, Apex speaks template lines only."""

    # GUESSED prices: derived from the 23 Sep balance drop (Rs 0.62 for 3884 tokens in and
    # 3496 out), assuming output costs 4x input as on aicredits' V3 price list.
    RS_PER_MILLION_IN = 35.0
    RS_PER_MILLION_OUT = 139.0

    def __init__(self, cap_rs=5.0):
        self.cap_rs = cap_rs
        self.spent_rs = 0.0

    def cost_of(self, tokens_in, tokens_out):
        return (
            tokens_in * self.RS_PER_MILLION_IN + tokens_out * self.RS_PER_MILLION_OUT
        ) / 1_000_000

    def charge(self, tokens_in, tokens_out):
        cost = self.cost_of(tokens_in, tokens_out)
        self.spent_rs += cost
        return cost

    def allows_llm(self):
        return self.spent_rs < self.cap_rs
