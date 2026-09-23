"""Turns Plaud notes/transcripts into the HTML that becomes a Google Doc."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import markdown as md

from .plaud import Recording

_TEXT_KEYS = ("text", "content", "sentence", "words", "transcript")
_SPEAKER_KEYS = ("speaker", "speaker_name", "speaker_label", "speaker_id", "spk")
_START_KEYS = ("start", "start_time", "start_ms", "begin", "begin_time", "offset", "timestamp")
_SEGMENT_LIST_KEYS = ("segments", "results", "transcript", "items", "data", "sentences", "list")
_SUMMARY_KEYS = ("summary", "markdown", "content", "note", "text", "ai_content", "abstract",
                 "note_list", "notes")


# ---------------------------------------------------------------- transcript
def transcript_segments(payload: Any) -> list[dict[str, Any]]:
    """Normalise a transcript payload into [{'start': s|None, 'speaker': str|None, 'text': str}]."""
    if isinstance(payload, str):
        return [{"start": None, "speaker": None, "text": line}
                for line in payload.splitlines() if line.strip()]
    if isinstance(payload, dict):
        for key in _SEGMENT_LIST_KEYS:
            if key in payload and payload[key]:
                return transcript_segments(payload[key])
        for key in _TEXT_KEYS:
            if isinstance(payload.get(key), str):
                return transcript_segments(payload[key])
        return []
    if isinstance(payload, list):
        segs = []
        for item in payload:
            if isinstance(item, str):
                segs.append({"start": None, "speaker": None, "text": item})
            elif isinstance(item, dict):
                text = next((item[k] for k in _TEXT_KEYS if isinstance(item.get(k), str)), "")
                if not text.strip():
                    continue
                speaker = next((item[k] for k in _SPEAKER_KEYS if item.get(k) not in (None, "")), None)
                start = next((item[k] for k in _START_KEYS if isinstance(item.get(k), (int, float))), None)
                segs.append({"start": start, "speaker": None if speaker is None else str(speaker),
                             "text": text.strip()})
        return segs
    return []


def _to_seconds(segs: list[dict[str, Any]], duration_ms: int | None) -> None:
    """Plaud tends to use milliseconds; detect and convert in place."""
    starts = [s["start"] for s in segs if s["start"] is not None]
    if not starts:
        return
    biggest = max(starts)
    if duration_ms:
        is_ms = biggest > (duration_ms / 1000) * 1.5
    else:
        is_ms = biggest > 86_400
    if is_ms:
        for s in segs:
            if s["start"] is not None:
                s["start"] = s["start"] / 1000


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _speaker_label(raw: str | None) -> str | None:
    if raw is None:
        return None
    return f"Speaker {int(raw) + 1}" if raw.isdigit() else raw


def transcript_html(payload: Any, duration_ms: int | None = None) -> str:
    segs = transcript_segments(payload)
    if not segs:
        return ""
    _to_seconds(segs, duration_ms)
    # Merge consecutive lines from the same speaker into one paragraph.
    blocks: list[dict[str, Any]] = []
    for s in segs:
        if blocks and s["speaker"] is not None and blocks[-1]["speaker"] == s["speaker"]:
            blocks[-1]["text"] += " " + s["text"]
        else:
            blocks.append(dict(s))
    parts = []
    for b in blocks:
        prefix = ""
        if b["start"] is not None:
            prefix += f'<span style="color:#888888">[{_hms(b["start"])}]</span> '
        label = _speaker_label(b["speaker"])
        if label:
            prefix += f"<b>{html.escape(label)}:</b> "
        parts.append(f"<p>{prefix}{html.escape(b['text'])}</p>")
    return "\n".join(parts)


# ------------------------------------------------------------------- summary
def summary_markdown(payload: Any) -> str:
    """Pull a Markdown summary out of a get_note payload."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        return "\n\n".join(filter(None, (summary_markdown(p) for p in payload)))
    if isinstance(payload, dict):
        pieces = []
        for key in _SUMMARY_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                pieces.append(val.strip())
                break
            if isinstance(val, (dict, list)) and val:
                inner = summary_markdown(val)
                if inner:
                    pieces.append(inner)
                    break
        for key, title in (("action_items", "Action items"), ("key_topics", "Key topics"),
                           ("topics", "Key topics"), ("todos", "Action items")):
            val = payload.get(key)
            if isinstance(val, list) and val:
                items = "\n".join(f"- {v if isinstance(v, str) else v.get('text') or v}" for v in val)
                pieces.append(f"### {title}\n{items}")
        return "\n\n".join(pieces)
    return ""


# -------------------------------------------------------------------- doc
def doc_title(rec: Recording) -> str:
    stamp = _pretty_date(rec.when, "%Y-%m-%d %H:%M")
    return f"{stamp} – {rec.name}" if stamp else rec.name


def _pretty_date(value: str | None, fmt: str) -> str | None:
    if not value:
        return None
    try:
        if value.isdigit():  # epoch seconds or ms
            n = int(value)
            dt = datetime.fromtimestamp(n / 1000 if n > 10**11 else n).astimezone()
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return dt.strftime(fmt)
    except (ValueError, OSError):
        return value


def build_doc_html(rec: Recording, summary_md: str, transcript_payload: Any,
                   summary_source: str = "Plaud") -> str:
    meta = []
    when = _pretty_date(rec.when, "%A, %B %d, %Y at %I:%M %p")
    if when:
        meta.append(f"<b>Recorded:</b> {html.escape(when)}")
    if rec.duration_ms:
        meta.append(f"<b>Duration:</b> {_hms(rec.duration_ms / 1000)}")
    meta.append(f"<b>Plaud ID:</b> {html.escape(rec.id)}")

    summary_body = (md.markdown(summary_md, extensions=["sane_lists"]) if summary_md
                    else "<p><i>No summary was available for this recording.</i></p>")
    if summary_md and summary_source != "Plaud":
        summary_body += f'<p style="color:#888888"><i>Summary generated by {html.escape(summary_source)}.</i></p>'
    body = transcript_html(transcript_payload, rec.duration_ms) or "<p><i>No transcript text.</i></p>"

    return f"""<html><head><meta charset="utf-8"></head><body>
<h1>{html.escape(rec.name)}</h1>
<p>{' &nbsp;·&nbsp; '.join(meta)}</p>
<h2>Summary</h2>
{summary_body}
<h2>Full transcript</h2>
{body}
</body></html>"""


def transcript_plain_text(payload: Any) -> str:
    segs = transcript_segments(payload)
    return "\n".join(
        (f"{_speaker_label(s['speaker'])}: " if s["speaker"] is not None else "") + s["text"]
        for s in segs
    )
