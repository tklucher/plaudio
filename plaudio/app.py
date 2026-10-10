"""What the desktop app's big red button does, kept free of any GUI code so it can be tested.

Same steps as `plaudio sync`: find recordings that haven't been exported yet and
write one Google Doc (or one Markdown file) for each.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .lock import single_instance
from .plaud import PlaudClient, PlaudError, first_error
from .state import State
from .sync import SyncOptions, SyncReport, run_sync

Echo = Callable[[str], None]


class SetupError(RuntimeError):
    """Something the user needs to fix (missing key, Node.js not installed, ...)."""


def default_markdown_dir() -> Path:
    return Path.home() / "Documents" / "Plaud Transcripts"


@dataclass
class AppSettings:
    google_client_id: str = ""
    google_client_secret: str = ""
    anthropic_api_key: str = ""     # optional: Claude writes a summary when Plaud has none
    markdown_mode: bool = False     # the switch: .md files instead of Google Docs
    markdown_dir: str = ""

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)  # holds keys; keep it private on Mac/Linux
        except OSError:
            pass

    @property
    def output_dir(self) -> Path:
        return Path(self.markdown_dir).expanduser() if self.markdown_dir.strip() else default_markdown_dir()


@dataclass
class JobResult:
    report: SyncReport
    location: str          # Drive folder URL or local folder path
    markdown: bool


def check_node(cfg: Config) -> None:
    exe = cfg.plaud_mcp_command[0]
    if exe in ("npx", "node") and not shutil.which(exe):
        raise SetupError(
            "Node.js isn't installed (or isn't on PATH), and this setup uses the "
            "Node.js Plaud connector.\n\nInstall the LTS version from https://nodejs.org, "
            "then restart PlaudIO.")


def _node_available(cfg: Config) -> bool:
    exe = cfg.plaud_mcp_command[0]
    return exe not in ("npx", "node") or bool(shutil.which(exe))


@asynccontextmanager
async def open_plaud(cfg: Config, echo: Echo, errlog: Any = None,
                     open_browser: Callable[[str], object] = webbrowser.open):
    """Connect to Plaud. Uses Plaud's hosted connector (no Node.js needed); if that
    fails and Node.js happens to be installed, falls back to the local connector."""
    if not cfg.plaud_url:
        check_node(cfg)
        async with PlaudClient(cfg.plaud_mcp_command, errlog) as plaud:
            yield plaud
        return

    from .plaud_auth import PlaudSignInError, make_oauth

    def browser(url: str):
        echo("Opening Plaud sign-in in your browser. Click Authorize there…")
        return open_browser(url)

    auth = make_oauth(cfg.plaud_url, cfg.plaud_token_path, port=cfg.plaud_redirect_port,
                      open_browser=browser)
    client = PlaudClient(url=cfg.plaud_url, auth=auth)
    try:
        await client.__aenter__()
    except BaseException as group:
        exc = first_error(group)
        if isinstance(exc, (PlaudSignInError, KeyboardInterrupt, asyncio.CancelledError)):
            raise SetupError(str(exc)) from exc
        if not _node_available(cfg):
            raise SetupError(f"Couldn't connect to Plaud ({exc}).\n\n"
                             "Check your internet connection and try again.") from exc
        echo(f"Plaud's online connection didn't work ({exc}). Trying the Node.js connector…")
        async with PlaudClient(cfg.plaud_mcp_command, errlog) as plaud:
            yield plaud
        return
    try:
        yield client
    finally:
        await client.__aexit__(None, None, None)


async def _ensure_plaud_login(plaud: PlaudClient, echo: Echo) -> None:
    if "get_current_user" not in plaud.tool_names:
        return  # nothing to check with; a sign-in problem will show up on the first call
    try:
        await plaud.whoami()
        return
    except PlaudError:
        if "login" not in plaud.tool_names:
            raise
    echo("Signing in to Plaud. Finish signing in in your browser…")
    await plaud.login()
    try:
        await plaud.whoami()
    except PlaudError as exc:
        raise SetupError("Plaud sign-in didn't finish. Complete it in your browser, "
                         "then press the button again.") from exc


async def sign_in_plaud(cfg: Config, echo: Echo, errlog: Any = None,
                        open_browser: Callable[[str], object] = webbrowser.open) -> None:
    echo("Connecting to Plaud (a browser window opens if you need to sign in)…")
    async with open_plaud(cfg, echo, errlog, open_browser) as plaud:
        if plaud.command and "login" in plaud.tool_names:   # local connector signs in via a tool
            result = await plaud.login()
            if result:
                echo(str(result) if not isinstance(result, (dict, list)) else json.dumps(result))
        if "get_current_user" in plaud.tool_names:
            who = await plaud.whoami()
            echo(f"Plaud account: {who if isinstance(who, str) else json.dumps(who)}")
        else:
            echo("Connected to Plaud.")


def _google_writer(cfg: Config, settings: AppSettings, echo: Echo):
    from .gdocs import DocsWriter, build_drive, client_config, get_credentials

    cid, secret = settings.google_client_id.strip(), settings.google_client_secret.strip()
    if bool(cid) != bool(secret):
        raise SetupError("Enter both the Google Client ID and the Google Client Secret.")
    if not cid and not cfg.google_client_secrets.exists() and not cfg.google_token_path.exists():
        raise SetupError("Enter your Google Client ID and Client Secret first, "
                         "or turn on the Markdown switch to save files instead.")
    echo("Checking Google sign-in (the first time, a browser window opens)…")
    creds = get_credentials(cfg.google_client_secrets, cfg.google_token_path, interactive=True,
                            client_cfg=client_config(cid, secret) if cid else None)
    return DocsWriter(build_drive(creds))


async def run_job(cfg: Config, settings: AppSettings, echo: Echo, errlog: Any = None,
                  opts: SyncOptions | None = None,
                  open_browser: Callable[[str], object] = webbrowser.open) -> JobResult:
    if settings.anthropic_api_key.strip():
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key.strip()

    with single_instance(cfg.lock_path):
        if settings.markdown_mode:
            from .mdfiles import MarkdownWriter
            out = settings.output_dir
            writer = MarkdownWriter(out)
            state = State(cfg.markdown_state_path)
            echo(f"Saving Markdown files to {out}")
        else:
            writer = await asyncio.to_thread(_google_writer, cfg, settings, echo)
            state = State(cfg.state_path)

        echo("Connecting to Plaud…")
        async with open_plaud(cfg, echo, errlog, open_browser) as plaud:
            await _ensure_plaud_login(plaud, echo)
            report = await run_sync(plaud, writer, state, cfg.folder_name, opts or SyncOptions(),
                                    echo=echo)

    if settings.markdown_mode:
        location = str(settings.output_dir)
    else:
        location = (f"https://drive.google.com/drive/folders/{state.folder_id}"
                    if state.folder_id else "")
    return JobResult(report=report, location=location, markdown=settings.markdown_mode)
