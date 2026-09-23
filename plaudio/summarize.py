"""Optional fallback: if Plaud has no AI summary for a recording and
ANTHROPIC_API_KEY is set, ask Claude to write one. No extra dependencies."""
from __future__ import annotations

import json
import os
import urllib.request

PROMPT = """Summarize this recording transcript for someone who wasn't there.
Write Markdown with: a 2-4 sentence overview, then "### Key points" (bullets),
then "### Action items" (bullets with owners if mentioned; omit if none).
Do not add a top-level heading.

<transcript>
{transcript}
</transcript>"""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def summarize(transcript_text: str, max_chars: int = 400_000) -> str:
    model = os.environ.get("PLAUDIO_SUMMARY_MODEL", "claude-haiku-4-5")
    body = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": PROMPT.format(transcript=transcript_text[:max_chars])}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
