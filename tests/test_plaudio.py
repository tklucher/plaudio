import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from plaudio import render
from plaudio.plaud import PlaudClient, Recording
from plaudio.state import State
from plaudio.sync import SyncOptions, run_sync

# The Claude summary fallback would change results if a key happens to be set.
os.environ.pop("ANTHROPIC_API_KEY", None)

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
    # big lookback: the fake recordings are dated Sept 2026, so keep the test date-proof
    _, rep = asyncio.run(_sync(tmp_path, w, require_summary=True, lookback_days=36500))
    assert "rec3" in [r.id for r in rep.skipped_not_ready]


def test_render_variants():
    assert render.transcript_segments("line a\n\nline b")[1]["text"] == "line b"
    html = render.transcript_html([{"start": 3.5, "speaker_id": "0", "text": "hi"}], duration_ms=10_000)
    assert "[00:00:03]" in html and "Speaker 1:" in html
    assert render.summary_markdown("# already md") == "# already md"
    assert render.summary_markdown({"note_list": [{"content": "x"}]}) == "x"
    rec = Recording(id="a", name="Standup", start_at="1758650000000")
    assert render.doc_title(rec).endswith("– Standup")


# ---------------------------------------------------------- markdown + app
def test_markdown_writer(tmp_path):
    from plaudio.mdfiles import MarkdownWriter, safe_filename
    out = tmp_path / "md"
    state = State(tmp_path / "state-markdown.json")

    async def go():
        async with PlaudClient(FAKE) as plaud:
            return await run_sync(plaud, MarkdownWriter(out), state, "x", SyncOptions(),
                                  echo=lambda s: None)
    rep = asyncio.run(go())
    files = sorted(out.glob("*.md"))
    assert len(rep.created) == 6 and len(files) == 6
    text = next(f for f in files if "Meeting 1" in f.name).read_text(encoding="utf-8")
    assert text.startswith("<!-- plaud_id: rec1 -->\n# Meeting 1")
    assert text.index("## Summary") < text.index("## Full transcript")
    assert "**Speaker 1:** Hello there. Let's begin." in text
    assert "`[00:01:05]`" in text and "Sounds \\<good\\> & fine." in text
    # lost state -> the files themselves stop duplicates
    (tmp_path / "state-markdown.json").unlink()
    state = State(tmp_path / "state-markdown.json")
    rep = asyncio.run(go())
    assert rep.created == [] and len(rep.already_in_drive) == 6
    assert safe_filename('a/b:c*?"<>|') == "a-b-c------"


def test_app_run_job_markdown(tmp_path):
    from plaudio.app import AppSettings, run_job
    from plaudio.config import Config
    cfg = Config(home=tmp_path, folder_name="Plaud Transcripts", plaud_mcp_command=FAKE)
    s = AppSettings(markdown_mode=True, markdown_dir=str(tmp_path / "out"))
    s.save(cfg.settings_path)
    assert AppSettings.load(cfg.settings_path) == s
    lines = []
    res = asyncio.run(run_job(cfg, s, lines.append))
    assert res.markdown and len(res.report.created) == 6
    assert res.location == str(tmp_path / "out")
    assert not (tmp_path / "state.json").exists()      # Google Docs history untouched
    res = asyncio.run(run_job(cfg, s, lines.append))
    assert res.report.created == []


def test_app_google_needs_keys(tmp_path):
    from plaudio.app import AppSettings, SetupError, _google_writer
    from plaudio.config import Config
    cfg = Config(home=tmp_path, folder_name="x", plaud_mcp_command=FAKE)
    for s in (AppSettings(), AppSettings(google_client_id="only-id")):
        try:
            _google_writer(cfg, s, lambda m: None)
            raise AssertionError("expected SetupError")
        except SetupError:
            pass


def test_lock_blocks_second_run(tmp_path):
    from plaudio.lock import AlreadyRunning, single_instance
    with single_instance(tmp_path / "l"):
        try:
            with single_instance(tmp_path / "l"):
                raise AssertionError("second lock should fail")
        except AlreadyRunning:
            pass
    with single_instance(tmp_path / "l"):
        pass


# ------------------------------------------------- Plaud's hosted connector
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager

HTTP_FAKE = Path(__file__).with_name("fake_plaud_http.py")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def hosted_plaud(access_ttl=3600):
    port = _free_port()
    env = dict(os.environ, FAKE_ACCESS_TTL=str(access_ttl))
    proc = subprocess.Popen([sys.executable, str(HTTP_FAKE), str(port)], env=env,
                            cwd=str(HTTP_FAKE.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(10)


class FakeBrowser:
    """Follows the sign-in redirect like a browser where the user clicks Authorize."""
    def __init__(self):
        self.opened = 0

    def __call__(self, url):
        self.opened += 1
        with urllib.request.urlopen(url, timeout=10) as r:
            assert b"Signed in to Plaud" in r.read()


def _hosted_cfg(tmp_path, url, command=None):
    from plaudio.config import Config
    return Config(home=tmp_path, folder_name="Plaud Transcripts",
                  plaud_mcp_command=command or ["npx-not-installed"], plaud_url=url,
                  plaud_redirect_port=_free_port())


def test_hosted_sign_in_once_then_reuse(tmp_path):
    from plaudio.app import AppSettings, run_job
    browser = FakeBrowser()
    s = AppSettings(markdown_mode=True, markdown_dir=str(tmp_path / "out"))
    with hosted_plaud() as url:
        cfg = _hosted_cfg(tmp_path, url)
        res = asyncio.run(run_job(cfg, s, lambda m: None, open_browser=browser))
        assert len(res.report.created) == 6 and browser.opened == 1
        assert cfg.plaud_token_path.exists()
        res = asyncio.run(run_job(cfg, s, lambda m: None, open_browser=browser))
        assert res.report.created == [] and browser.opened == 1   # no second sign-in


def test_hosted_token_refresh(tmp_path):
    from plaudio.app import open_plaud
    browser = FakeBrowser()

    async def whoami(cfg):
        async with open_plaud(cfg, lambda m: None, open_browser=browser) as plaud:
            return await plaud.whoami()

    with hosted_plaud(access_ttl=1) as url:
        cfg = _hosted_cfg(tmp_path, url)
        assert asyncio.run(whoami(cfg))["email"]
        time.sleep(2.5)                                            # access token expired
        assert asyncio.run(whoami(cfg))["email"] and browser.opened == 1


def test_hosted_unreachable_falls_back_to_local(tmp_path):
    from plaudio.app import open_plaud
    cfg = _hosted_cfg(tmp_path, f"http://127.0.0.1:{_free_port()}/mcp", command=FAKE)
    lines = []

    async def go():
        async with open_plaud(cfg, lines.append, open_browser=FakeBrowser()) as plaud:
            return plaud.command is not None, len(await plaud.list_recordings())
    assert asyncio.run(go()) == (True, 7)
    assert any("Node.js connector" in line for line in lines)


def test_hosted_unreachable_without_node_explains(tmp_path):
    from plaudio.app import SetupError, open_plaud
    cfg = _hosted_cfg(tmp_path, f"http://127.0.0.1:{_free_port()}/mcp", command=["npx"])
    os.environ["PATH"], old = "", os.environ.get("PATH", "")

    async def go():
        async with open_plaud(cfg, lambda m: None, open_browser=FakeBrowser()):
            pass
    try:
        asyncio.run(go())
        raise AssertionError("expected SetupError")
    except SetupError as exc:
        assert "Couldn't connect to Plaud" in str(exc)
    finally:
        os.environ["PATH"] = old
