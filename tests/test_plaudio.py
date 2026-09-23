import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from plaudio import render
from plaudio.plaud import PlaudClient, Recording
from plaudio.state import State
from plaudio.sync import SyncOptions, run_sync

FAKE = [sys.executable, str(Path(__file__).with_name("fake_plaud_mcp.py"))]


class FakeWriter:
    def __init__(self):
        self.docs = {}
        self.folders = 0

    def ensure_folder(self, folder_id, name):
        if folder_id:
            return folder_id
        self.folders += 1
        return "folder1"

    def find_existing(self, folder_id, rid):
        return next((d for d, v in self.docs.items() if v["rid"] == rid), None)

    def create_doc(self, folder_id, title, html, rid):
        doc_id = f"doc_{rid}"
        self.docs[doc_id] = {"rid": rid, "title": title, "html": html, "folder": folder_id}
        return doc_id


async def _sync(tmp_path, writer, **kw):
    state = State(tmp_path / "state.json")
    async with PlaudClient(FAKE) as plaud:
        return state, await run_sync(plaud, writer, state, "Plaud Transcripts", SyncOptions(**kw),
                                     echo=lambda s: None)


def test_first_run_then_incremental(tmp_path):
    w = FakeWriter()
    state, rep = asyncio.run(_sync(tmp_path, w))
    assert len(rep.created) == 6                       # rec5 has no transcript yet
    assert [r.id for r in rep.skipped_not_ready] == ["rec5"]
    assert w.folders == 1 and state.folder_id == "folder1"
    doc = w.docs["doc_rec1"]["html"]
    assert doc.index("<h2>Summary</h2>") < doc.index("<h2>Full transcript</h2>")
    assert "<strong>Overview</strong>" in doc and "Send follow-up" in doc
    assert "Hello there. Let&#x27;s begin." in doc          # same speaker merged
    assert "[00:01:05]" in doc                               # ms -> h:m:s
    assert "Sounds &lt;good&gt; &amp; fine." in doc          # escaped
    assert "No summary was available" in w.docs["doc_rec3"]["html"]

    # Second run: nothing new, no duplicates, no new folder.
    state2, rep2 = asyncio.run(_sync(tmp_path, w))
    assert rep2.created == [] and len(w.docs) == 6 and w.folders == 1


def test_lost_state_does_not_duplicate(tmp_path):
    w = FakeWriter()
    asyncio.run(_sync(tmp_path, w))
    (tmp_path / "state.json").unlink()
    state = State(tmp_path / "state.json"); state.folder_id = "folder1"; state.save()
    _, rep = asyncio.run(_sync(tmp_path, w))
    assert rep.created == [] and len(rep.already_in_drive) == 6


def test_mark_existing_and_dry_run(tmp_path):
    w = FakeWriter()
    _, rep = asyncio.run(_sync(tmp_path, w, dry_run=True))
    assert len(rep.created) == 6 and w.docs == {}
    _, rep = asyncio.run(_sync(tmp_path, w, mark_only=True))
    assert rep.marked == 7
    _, rep = asyncio.run(_sync(tmp_path, w))
    assert rep.created == [] and w.docs == {}


def test_limit_and_require_summary(tmp_path):
    w = FakeWriter()
    _, rep = asyncio.run(_sync(tmp_path, w, limit=2, require_summary=True))
    assert [r.id for r, _ in rep.created] == ["rec1", "rec2"]
    _, rep = asyncio.run(_sync(tmp_path, w, require_summary=True))
    assert "rec3" in [r.id for r in rep.skipped_not_ready]


def test_render_variants():
    assert render.transcript_segments("line a\n\nline b")[1]["text"] == "line b"
    html = render.transcript_html([{"start": 3.5, "speaker_id": "0", "text": "hi"}], duration_ms=10_000)
    assert "[00:00:03]" in html and "Speaker 1:" in html
    assert render.summary_markdown("# already md") == "# already md"
    assert render.summary_markdown({"note_list": [{"content": "x"}]}) == "x"
    rec = Recording(id="a", name="Standup", start_at="1758650000000")
    assert render.doc_title(rec).endswith("– Standup")
