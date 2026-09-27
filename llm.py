"""The one door to the language model: the provider, the model names and the client.

aicredits.in serves DeepSeek through an OpenAI-compatible API; the key is AICREDITS_API_KEY in
.env. Until 27 Sep 2026 the coach, the persona and the debrief each built this client
themselves, and the debrief's model name sat in its own code."""

import os

PROVIDER_URL = "https://aicredits.in/v1"
LIVE_MODEL = (
    "deepseek-v4-flash"  # during a session: the push-to-talk coach, line phrasing
)
DEBRIEF_MODEL = "deepseek-v4.1-flash"  # after the race: the debrief
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


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
