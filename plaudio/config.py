"""Paths and settings. Everything lives in ~/.plaudio unless PLAUDIO_HOME is set."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    folder_name: str
    plaud_mcp_command: list[str]

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

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
    cmd = os.environ.get("PLAUDIO_MCP_COMMAND", "npx -y @plaud-ai/mcp@latest").split()
    return Config(
        home=home,
        folder_name=os.environ.get("PLAUDIO_FOLDER_NAME", "Plaud Transcripts"),
        plaud_mcp_command=cmd,
    )
