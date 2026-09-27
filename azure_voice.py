"""Azure Speech: Microsoft's neural voices WITH emotions (25 Sep 2026).

Why: the cloned Max voice ate the GPU next to LMU (100% busy, lines 7-17 s late, "can't hear
a damn thing") and was dropped, his call. edge-tts is free and uses no GPU, but Microsoft
blocks its emotion styles (only rate, pitch and volume). Azure's own Speech service has the
same voices with real styles (Davis: excited, shouting, angry, cheerful, friendly ...), runs in
the cloud, and its free tier (F0) gives 0.5 million characters a month - about 80 races.

    .env:  AZURE_SPEECH_KEY=...   AZURE_SPEECH_REGION=centralindia

No key, a timeout, or the monthly quota used up: render() returns None and the caller says
the line with edge-tts, so the radio never goes silent. Output is 24 kHz 16-bit mono WAV, the
phrase bank's format.
"""

import os
import time
from xml.sax.saxutils import escape

ENGINEER = "en-US-DavisNeural"
SPOTTER = "en-US-GuyNeural"
TIMEOUT_S = 4.0
FREE_TIER_PER_MINUTE = (
    20  # F0: 20 transactions per 60 s, not adjustable (Microsoft quotas page)
)
OUTPUT = "riff-24khz-16bit-mono-pcm"

# (voice role, mood) -> (style, style degree 0.01-2, speaking rate). Moods come from
# voice.mood_of(kind): urgent (fights, flags, spotter), fired (praise, attack), dry (the rest).
# GUESSED until he has heard the samples (tools/azure_samples.py).
STYLES = {
    ("engineer", "urgent"): ("shouting", 0.7, "+8%"),
    ("engineer", "fired"): ("excited", 1.6, "+6%"),
    ("engineer", "dry"): ("chat", 1.0, "+4%"),
    ("spotter", "urgent"): ("shouting", 0.6, "+10%"),
    ("spotter", "fired"): ("excited", 1.2, "+10%"),
    ("spotter", "dry"): ("chat", 1.0, "+8%"),
}


def ssml(text, voice, style, degree, rate):
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='en-US'>"
        f"<voice name='{voice}'><mstts:express-as style='{style}' styledegree='{degree}'>"
        f"<prosody rate='{rate}'>{escape(text)}</prosody></mstts:express-as></voice></speak>"
    )


class AzureVoice:
    def __init__(self, key=None, region=None, timeout=TIMEOUT_S, post=None):
        if key is None or region is None:
            try:
                from dotenv import load_dotenv

                load_dotenv(
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
                )
            except ImportError:
                pass
        self.key = key or os.environ.get("AZURE_SPEECH_KEY", "").strip()
        self.region = region or os.environ.get("AZURE_SPEECH_REGION", "").strip()
        self.timeout = timeout
        self.post = post  # tests hand in a stand-in for requests.post
        self.failures = 0
        self.last_error = None

    @property
    def ready(self):
        # after 3 failures in a row (quota, key, network) stop trying: edge-tts says the rest
        return bool(self.key and self.region) and self.failures < 3

    def render(self, text, role="engineer", mood="dry", timeout=None):
        """WAV bytes, or None (not set up, too slow, over quota) to fall back to edge-tts."""
        if not self.ready:
            return None
        style, degree, rate = STYLES.get((role, mood), STYLES[(role, "dry")])
        voice = SPOTTER if role == "spotter" else ENGINEER
        post = self.post
        if post is None:
            import requests

            post = requests.post
        try:
            answer = post(
                f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
                data=ssml(text, voice, style, degree, rate).encode("utf8"),
                headers={
                    "Ocp-Apim-Subscription-Key": self.key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": OUTPUT,
                    "User-Agent": "apex",
                },
                timeout=timeout or self.timeout,
            )
        except Exception as error:
            self.failures += 1
            self.last_error = error.__class__.__name__
            return None
        if answer.status_code == 429:
            # the free tier allows 20 requests a minute: this line goes to edge-tts, Azure stays on
            self.last_error = "HTTP 429"
            return None
        if answer.status_code != 200 or not answer.content.startswith(b"RIFF"):
            self.failures += 1
            self.last_error = f"HTTP {answer.status_code}"
            return None
        self.failures = 0
        return answer.content

    def timed(self, text, role="engineer", mood="dry"):
        started = time.perf_counter()
        audio = self.render(text, role, mood)
        return audio, round((time.perf_counter() - started) * 1000)
