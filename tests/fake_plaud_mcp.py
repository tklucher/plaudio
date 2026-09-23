"""A stand-in for @plaud-ai/mcp used by the tests (same tool names/params)."""
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-plaud")

FILES = [
    {"id": f"rec{i}", "name": f"Meeting {i}", "created_at": f"2026-09-{10+i:02d}T17:00:00Z",
     "start_at": f"2026-09-{10+i:02d}T16:30:00Z", "duration": 125_000, "serial_number": "X1"}
    for i in range(1, 8)
]


@mcp.tool()
def get_current_user() -> str:
    return json.dumps({"email": "tom@example.com"})


@mcp.tool()
def list_files(query: str = "", date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 20) -> str:
    items = [f for f in FILES if not date_from or f["start_at"][:10] >= date_from]
    chunk = items[(page - 1) * page_size: page * page_size]
    return json.dumps({"total": len(items), "files": chunk})


@mcp.tool()
def get_note(file_id: str) -> str:
    if file_id == "rec3":
        return json.dumps({"note_list": []})
    return json.dumps({"summary": f"**Overview** of {file_id}.\n\n- point one\n- point two",
                       "action_items": ["Send follow-up"]})


@mcp.tool()
def get_transcript(file_id: str) -> str:
    if file_id == "rec5":
        return json.dumps({"segments": []})  # not ready yet
    return json.dumps({"segments": [
        {"start_time": 0, "end_time": 4000, "speaker": "Speaker 1", "content": "Hello there."},
        {"start_time": 4000, "end_time": 8000, "speaker": "Speaker 1", "content": "Let's begin."},
        {"start_time": 65000, "end_time": 70000, "speaker": "Tom", "content": "Sounds <good> & fine."},
    ]})


if __name__ == "__main__":
    mcp.run()
