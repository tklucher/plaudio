# plaudio

Copies new Plaud recordings into Google Docs. Each run creates one Google Doc per
recording that hasn't been exported yet. The doc has the Plaud AI summary at the top
and the full transcript (with timestamps and speakers) below it. Run it by hand
or on a schedule a few times a day.

```
Plaud account ──(Plaud MCP server)──▶ plaudio ──(Drive API)──▶ "Plaud Transcripts" folder
                                          │
                                   ~/.plaudio/state.json  (what's already been exported)
```

## Desktop app (Windows)

If you'd rather not use the command line, use **PlaudIO.exe**. It does exactly what
`plaudio sync` does, in a small window:

1. Type your **Google Client ID** and **Google Client Secret** (step 3 below shows where
   to get them). The **Anthropic API key** is optional; with it, Claude writes a summary
   when Plaud doesn't have one.
2. Press the **big red button**. It checks Plaud for recordings that haven't been
   exported yet and creates a Google Doc for each one. The first time, browser windows
   open so you can sign in to Plaud and Google.
3. Turn on the **Markdown switch** to save `.md` files in a folder on your PC instead of
   creating Google Docs (no Google keys needed then). The default folder is
   `Documents\Plaud Transcripts`.

Your keys and choices are saved in `%USERPROFILE%\.plaudio\settings.json`, so you
only type them once. Markdown exports keep their own history (`state-markdown.json`),
so a recording saved as Markdown can still be sent to Google Docs later.

**Nothing else to install.** PlaudIO talks to Plaud's own online connector, so it doesn't
need Node.js. The first time, a browser window opens for Plaud sign-in; after that the
sign-in is remembered (`plaud_oauth.json` in the same folder). **Plaud Cloud Sync must
be turned on** in the Plaud app, because the connector only sees synced recordings.

### Getting PlaudIO.exe

- **Download it:** every push builds the app on GitHub (Actions → "Build Windows app"
  → the latest run → *PlaudIO-windows* artifact). Pushing a tag like `v0.2.0` also
  attaches the .exe to a GitHub Release.
- **Or build it yourself** on a Windows PC with Python 3.10+ installed: double-click
  `build_windows.bat`. The app appears in `dist\PlaudIO.exe`.

The .exe isn't code-signed, so the first time Windows may show "Windows protected your
PC". Click **More info → Run anyway**.

If you installed with pip, `plaudio-gui` opens the same window on any OS.

## Why not the "Plaud Embedded" API?

Plaud Embedded (docs.plaud.ai/plaud-embedded) is a partner platform for building your
own apps around Plaud devices. It transcribes audio **you upload** and has no endpoint
that lists the recordings in your Plaud account. Plaud's official way to read your own
recordings is the **Plaud MCP server** (docs.plaud.ai/plaud-mcp-cli), which signs in
with your normal Plaud account. plaudio connects to Plaud's hosted copy of it at
`https://mcp.plaud.ai/mcp` (standard MCP sign-in in the browser, no Node.js needed) and
calls its `list_files`, `get_note` and `get_transcript` tools.

If that online connection fails and Node.js happens to be installed, plaudio falls back
to running the same server locally (`npx @plaud-ai/mcp`), which is how version 0.1 worked.

## Setup (one time, about 15 minutes)

### 1. Install

You need **Python 3.11+**. (Node.js is optional: it's only used as a fallback.)

```bash
cd plaudio
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Sign in to Plaud

```bash
plaudio login
```

A browser window opens for Plaud sign-in; click **Authorize**. Check that it worked
with `plaudio inspect`, which lists your 10 most recent recordings. `plaudio logout`
forgets the saved sign-in.

### 3. Create Google credentials

1. Go to https://console.cloud.google.com and create a project (for example "plaudio").
2. **APIs & Services → Library** → enable **Google Drive API**.
3. **APIs & Services → OAuth consent screen** (called "Google Auth Platform" in newer
   consoles). Choose **External**, fill in the app name and your email, and add
   yourself as a test user.
4. **Important for scheduled runs:** set the publishing status to **In production**.
   Apps left in "Testing" get Google refresh tokens that expire after 7 days, so the
   scheduled sync would stop working every week. You don't need Google's verification
   for your own use. You'll just see an "unverified app" warning once when you sign in.
5. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download
   the JSON and save it as `~/.plaudio/credentials.json`.

```bash
plaudio auth-google
```

This opens a browser once and saves a token. plaudio only asks for the `drive.file`
scope, so it can see only the files and folders it creates. The rest of your Drive
stays off-limits to it.

### 4. First sync

```bash
plaudio sync --dry-run          # preview: what would be created?
plaudio sync                    # export everything (first run = full history)
```

If you **don't** want your whole back catalogue exported, run this once first:

```bash
plaudio sync --mark-existing    # treat everything that exists today as done
```

The first real sync creates a Drive folder called **Plaud Transcripts** and prints its
link. You can move or rename that folder anywhere in your Drive and plaudio will keep
using it, because it's tracked by ID.

## Running it a few times a day

**macOS / Linux (cron).** Run `crontab -e` and add this line to run at 8am, noon, 4pm
and 8pm:

```cron
0 8,12,16,20 * * *  PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin  /full/path/to/plaudio/.venv/bin/plaudio sync >> ~/.plaudio/sync.log 2>&1
```

Scheduled runs reuse the saved Plaud sign-in and renew it quietly. On macOS you may
need to give `cron` Full Disk Access, or use a launchd agent instead.

**Windows.** In Task Scheduler, create a task that runs
`C:\path\to\plaudio\.venv\Scripts\plaudio.exe sync` on a daily trigger that repeats
every 4 hours.

A lock file stops two runs from overlapping.

## Commands

| Command | What it does |
|---|---|
| `plaudio sync` | Export new recordings. Options: `--dry-run`, `--limit N`, `--full` (rescan everything), `--lookback-days N` (default 14), `--require-summary`, `--mark-existing`, `--markdown DIR` (save .md files instead of Google Docs) |
| `plaudio-gui` | Open the desktop app |
| `plaudio status` | Last run time, folder link, recently exported docs |
| `plaudio inspect [ID]` | Show raw Plaud data. With no ID it lists recent recordings |
| `plaudio login` / `plaudio auth-google` | Sign in again |
| `plaudio logout` | Forget the saved Plaud sign-in |

## How "only new ones" works

- `~/.plaudio/state.json` records every recording ID that has been exported. It's
  saved after each doc, so a crash part-way through never creates duplicates.
- Each run re-scans the last 14 days before the previous run. This catches recordings
  that sync from the device late, or that weren't transcribed yet last time.
- Recordings with no transcript yet are skipped and retried on the next run.
- Each doc is tagged with its Plaud ID in Drive. If the state file is ever lost,
  plaudio sees the existing docs and doesn't create them again.

## Summaries

The summary comes from Plaud's own AI note (`get_note`). If a recording has no Plaud
summary:

- by default the doc is still created, with a "No summary was available" note;
- with `--require-summary`, the recording waits until a summary exists;
- if `ANTHROPIC_API_KEY` is set, Claude writes the summary from the transcript and the
  doc says so. You can change the model with `PLAUDIO_SUMMARY_MODEL`
  (default `claude-haiku-4-5`).

## Settings (environment variables)

| Variable | Default |
|---|---|
| `PLAUDIO_HOME` | `~/.plaudio` (state, tokens, credentials) |
| `PLAUDIO_FOLDER_NAME` | `Plaud Transcripts` (name used when the folder is first created) |
| `PLAUDIO_PLAUD_URL` | `https://mcp.plaud.ai/mcp` (set to `local` to always use the Node.js connector) |
| `PLAUDIO_MCP_COMMAND` | `npx -y @plaud-ai/mcp@latest` (the local connector; setting this also turns off the online one) |
| `ANTHROPIC_API_KEY` | unset (turns on the Claude summary fallback) |

## If the docs look wrong

Plaud documents the MCP tool names and parameters but not their exact output. plaudio
parses the output defensively (JSON or text, several common field names). If a doc
comes out empty or oddly formatted, run:

```bash
plaudio inspect <recording-id>
```

and adjust `plaudio/render.py` to match the field names you see.

## Tests

```bash
python tests/run.py      # or: pytest
```

The tests run the real MCP client against fake Plaud servers and use a fake Drive
writer: `tests/fake_plaud_mcp.py` stands in for the local connector, and
`tests/fake_plaud_http.py` for the hosted one, including the full browser sign-in
(registration, PKCE, token refresh).
