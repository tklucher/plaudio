"""Paths and settings. Everything lives in ~/.plaudio unless PLAUDIO_HOME is set."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    folder_name: str
    plaud_mcp_command: list[str]          # local connector (needs Node.js); used as fallback
    plaud_url: str | None = None          # Plaud's hosted connector; None = local only
    plaud_redirect_port: int = 8199       # local port the browser returns to after Plaud sign-in

    @property
    def plaud_token_path(self) -> Path:
        return self.home / "plaud_oauth.json"

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    @property
    def markdown_state_path(self) -> Path:
        # Kept separate from state.json so exporting to Markdown never stops a
        # recording from also going to Google Docs later (and vice versa).
        return self.home / "state-markdown.json"

    @property
    def settings_path(self) -> Path:
        return self.home / "settings.json"  # keys and choices saved by the desktop app

    @property
    def google_client_secrets(self) -> Path:
        return self.home / "credentials.json"

    @property
    def google_token_path(self) -> Path:
        return self.home / "google_token.json"

    @property
    def lock_path(self) -> Path:
        return self.home / "sync.lock"


def load_config() -> Config:
    home = Path(os.environ.get("PLAUDIO_HOME", Path.home() / ".plaudio")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    custom_cmd = os.environ.get("PLAUDIO_MCP_COMMAND")
    cmd = (custom_cmd or "npx -y @plaud-ai/mcp@latest").split()
    # Default: Plaud's hosted connector (no Node.js needed). Setting PLAUDIO_MCP_COMMAND,
    # or PLAUDIO_PLAUD_URL=local, switches to running a local connector instead.
    url = os.environ.get("PLAUDIO_PLAUD_URL", "https://mcp.plaud.ai/mcp")
    if custom_cmd or url.lower() in ("", "local", "none"):
        url = None
    return Config(
        home=home,
        folder_name=os.environ.get("PLAUDIO_FOLDER_NAME", "Plaud Transcripts"),
        plaud_mcp_command=cmd,
        plaud_url=url,
    )
