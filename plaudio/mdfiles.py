"""Writes each recording as a Markdown (.md) file in a local folder, instead of a Google Doc.

Has the same interface as gdocs.DocsWriter so sync.run_sync can use either.
"""
from __future__ import annotations

import re
from pathlib import Path

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in Windows file names


def safe_filename(title: str, max_len: int = 120) -> str:
    name = _BAD_CHARS.sub("-", title).strip(" .")
    return (name[:max_len].rstrip(" .") or "recording")


class MarkdownWriter:
    content_format = "markdown"

    def __init__(self, out_dir: Path | str):
        self.out_dir = Path(out_dir).expanduser()

    def ensure_folder(self, folder_id: str | None, name: str) -> str:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return str(self.out_dir)

    def find_existing(self, folder_id: str, recording_id: str) -> str | None:
        """Safety net against duplicates: look for a file already tagged with this ID."""
        marker = f"<!-- plaud_id: {recording_id} -->"
        for path in Path(folder_id).glob("*.md"):
            try:
                with open(path, encoding="utf-8") as fh:
                    if fh.readline().strip() == marker:
                        return str(path)
            except OSError:
                continue
        return None

    def create_doc(self, folder_id: str, title: str, body: str, recording_id: str) -> str:
        folder = Path(folder_id)
        base = safe_filename(title)
        path = folder / f"{base}.md"
        n = 2
        while path.exists():  # two recordings with the same title and minute
            path = folder / f"{base} ({n}).md"
            n += 1
        path.write_text(body, encoding="utf-8")
        return str(path)

    @staticmethod
    def link(doc_id: str) -> str:
        return Path(doc_id).name  # the folder is shown once at the start; keep log lines short
