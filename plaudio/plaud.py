"""Talks to Plaud through the official Plaud MCP server (@plaud-ai/mcp) over stdio.

Why MCP and not the "Plaud Embedded" REST API? The Embedded API is a partner
platform for building your own device apps: it can transcribe audio you upload,
but it has no endpoint that lists the recordings in *your* Plaud account. The
Plaud MCP server (and the `plaud` CLI) are Plaud's official way to read your own
recordings, transcripts and AI summaries, so this client speaks MCP to it.
"""
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class PlaudError(RuntimeError):
    pass


@dataclass
class Recording:
    id: str
    name: str
    created_at: str | None = None
    start_at: str | None = None
    duration_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Recording":
        duration = d.get("duration")
        return cls(
            id=str(d["id"]),
            name=str(d.get("name") or d.get("title") or d.get("filename") or d["id"]),
            created_at=_as_str(d.get("created_at")),
            start_at=_as_str(d.get("start_at")),
            duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
            raw=d,
        )

    @property
    def when(self) -> str | None:
        return self.start_at or self.created_at


def _as_str(v: Any) -> str | None:
    return None if v is None else str(v)


# --------------------------------------------------------------------------
# Result parsing. The MCP tools return JSON text; we parse defensively so small
# format changes on Plaud's side don't break the sync.
# --------------------------------------------------------------------------
def result_payload(result: Any) -> Any:
    """Turn an MCP CallToolResult into Python data (dict/list) or plain text."""
    if getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise PlaudError(text or "Plaud MCP tool returned an error")
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        pass
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]  # FastMCP-style wrapper
    if isinstance(structured, str):
        try:
            return json.loads(structured)
        except (json.JSONDecodeError, ValueError):
            return structured
    return structured if structured else joined


def find_recordings(payload: Any) -> list[dict[str, Any]]:
    """Find the list of recording dicts anywhere inside a list_files payload."""
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) and "id" in x for x in payload):
            return payload
        for item in payload:
            found = find_recordings(item)
            if found:
                return found
    elif isinstance(payload, dict):
        for key in ("files", "items", "data", "list", "records", "recordings", "result"):
            if key in payload:
                found = find_recordings(payload[key])
                if found:
                    return found
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = find_recordings(value)
                if found:
                    return found
    return []


class PlaudClient:
    """Async context manager wrapping one Plaud MCP server subprocess."""

    def __init__(self, command: list[str]):
        self.command = command
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self._id_params: dict[str, str] = {}

    async def __aenter__(self) -> "PlaudClient":
        params = StdioServerParameters(command=self.command[0], args=self.command[1:])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        tools = await self.session.list_tools()
        # Learn each tool's ID parameter name from its schema (e.g. "file_id" vs "id").
        for tool in tools.tools:
            schema = tool.inputSchema or {}
            required = schema.get("required") or list((schema.get("properties") or {}).keys())
            if required:
                self._id_params[tool.name] = required[0]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        assert self.session is not None
        result = await self.session.call_tool(tool, args or {})
        return result_payload(result)

    async def _call_with_id(self, tool: str, recording_id: str) -> Any:
        key = self._id_params.get(tool, "file_id")
        return await self.call(tool, {key: recording_id})

    # --- high-level operations ------------------------------------------
    async def login(self) -> Any:
        return await self.call("login")

    async def whoami(self) -> Any:
        return await self.call("get_current_user")

    async def list_recordings(
        self, date_from: date | None = None, page_size: int = 50, max_pages: int = 200
    ) -> list[Recording]:
        out: list[Recording] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            args: dict[str, Any] = {"page": page, "page_size": page_size}
            if date_from:
                args["date_from"] = date_from.isoformat()
            batch = find_recordings(await self.call("list_files", args))
            new = [r for r in batch if str(r["id"]) not in seen]
            if not new:
                break
            for r in new:
                seen.add(str(r["id"]))
                out.append(Recording.from_dict(r))
            if len(batch) < page_size:
                break
        return out

    async def get_transcript(self, recording_id: str) -> Any:
        return await self._call_with_id("get_transcript", recording_id)

    async def get_note(self, recording_id: str) -> Any:
        return await self._call_with_id("get_note", recording_id)
