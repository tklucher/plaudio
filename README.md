# plaudio – design notes (v0.1, 2026-09-23)
 
Goal: CLI run a few times a day that creates one Google Doc per new Plaud recording (summary on top, full transcript below) in a Drive folder.
 
## Key decisions
- **Plaud access via Plaud MCP server (`npx @plaud-ai/mcp`), not Plaud Embedded API.** Embedded is a partner/device-app platform (upload audio → transcribe); it has no endpoint to list a user's own recordings. MCP tools used: `login`, `get_current_user`, `list_files(query,date_from,date_to,page,page_size)`, `get_note(id)`, `get_transcript(id)`. The ID param name is read from each tool's input schema.
- MCP output format is undocumented → parsing is defensive (render.py); `plaudio inspect <id>` dumps raw payloads to tune it.
- **Google:** Drive v3 upload of HTML with target mimeType `application/vnd.google-apps.document` (auto-converts). Scope `drive.file` only; app creates a "Plaud Transcripts" folder and tracks it by ID. OAuth app must be "In production", otherwise refresh tokens expire after 7 days.
- **"New only":** `~/.plaudio/state.json` of processed IDs (saved after each doc) + 14-day lookback re-scan + Drive `appProperties.plaud_id` dedupe as a safety net. Recordings with no transcript yet are skipped and retried later.
- Summary = Plaud AI note; optional Claude fallback when `ANTHROPIC_API_KEY` is set; `--require-summary` waits for one.
- Lock file prevents overlapping cron runs.
## Status / open items
- Tested only against a fake MCP server + fake Drive writer (Google libs weren't installable in the build sandbox). The first real run should confirm the real `list_files`/`get_note`/`get_transcript` payload shapes.
- Unknown: whether MCP token refresh works unattended long-term.
