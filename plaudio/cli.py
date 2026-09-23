"""plaudio – copy new Plaud recordings into Google Docs.

  plaudio login          sign in to Plaud (opens a browser)
  plaudio auth-google    sign in to Google (opens a browser)
  plaudio sync           create a Doc for every recording not yet exported
  plaudio status         show what has been exported so far
  plaudio inspect ID     print the raw Plaud data for one recording (debugging)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

from .config import Config, load_config
from .state import State


@contextmanager
def single_instance(lock_path: Path):
    """Stop two scheduled runs from overlapping and creating duplicate docs."""
    try:
        import fcntl
    except ImportError:  # Windows: skip locking
        yield
        return
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another plaudio sync is already running; exiting.", file=sys.stderr)
            sys.exit(0)
        yield


def _plaud(cfg: Config):
    from .plaud import PlaudClient
    return PlaudClient(cfg.plaud_mcp_command)


async def cmd_login(cfg: Config, _args) -> int:
    async with _plaud(cfg) as plaud:
        print(await plaud.login())
        print(await plaud.whoami())
    return 0


def cmd_auth_google(cfg: Config, _args) -> int:
    from .gdocs import get_credentials
    get_credentials(cfg.google_client_secrets, cfg.google_token_path, interactive=True)
    print(f"Google sign-in saved to {cfg.google_token_path}")
    return 0


async def cmd_sync(cfg: Config, args) -> int:
    from .gdocs import DocsWriter, GoogleSetupError, build_drive, get_credentials
    from .sync import SyncOptions, run_sync

    opts = SyncOptions(dry_run=args.dry_run, full=args.full, lookback_days=args.lookback_days,
                       limit=args.limit, require_summary=args.require_summary,
                       mark_only=args.mark_existing)
    with single_instance(cfg.lock_path):
        state = State(cfg.state_path)
        writer = None
        if not (opts.dry_run or opts.mark_only):
            try:
                creds = get_credentials(cfg.google_client_secrets, cfg.google_token_path,
                                        interactive=sys.stdin.isatty())
            except GoogleSetupError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            writer = DocsWriter(build_drive(creds))
        async with _plaud(cfg) as plaud:
            report = await run_sync(plaud, writer, state, cfg.folder_name, opts)
        if state.folder_id and report.created and not opts.dry_run:
            print(f"Folder: https://drive.google.com/drive/folders/{state.folder_id}")
        return 0 if report.ok else 1


def cmd_status(cfg: Config, _args) -> int:
    state = State(cfg.state_path)
    print(f"State file : {cfg.state_path}")
    print(f"Last run   : {state.last_run or 'never'}")
    print(f"Exported   : {len(state.data['processed'])} recording(s)")
    if state.folder_id:
        print(f"Folder     : https://drive.google.com/drive/folders/{state.folder_id}")
    recent = sorted(state.data["processed"].items(), key=lambda kv: kv[1]["at"])[-10:]
    for rid, info in recent:
        link = f"https://docs.google.com/document/d/{info['doc_id']}" if info["doc_id"] else "(marked only)"
        print(f"  {info['at']}  {info['name']}  {link}")
    return 0


async def cmd_inspect(cfg: Config, args) -> int:
    async with _plaud(cfg) as plaud:
        if not args.recording_id:
            recs = await plaud.list_recordings(page_size=10, max_pages=1)
            for r in recs:
                print(json.dumps(r.raw, default=str))
            return 0
        for tool in ("get_file", "get_note", "get_transcript"):
            print(f"===== {tool} =====")
            try:
                data = await plaud._call_with_id(tool, args.recording_id)
                print(json.dumps(data, indent=2, default=str)[:5000]
                      if not isinstance(data, str) else data[:5000])
            except Exception as exc:
                print(f"error: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="plaudio", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="sign in to Plaud")
    sub.add_parser("auth-google", help="sign in to Google")
    s = sub.add_parser("sync", help="export new recordings to Google Docs")
    s.add_argument("--dry-run", action="store_true", help="show what would be created")
    s.add_argument("--full", action="store_true", help="scan all recordings, not just recent ones")
    s.add_argument("--lookback-days", type=int, default=14,
                   help="how far before the last run to re-scan (default 14)")
    s.add_argument("--limit", type=int, help="create at most N docs this run")
    s.add_argument("--require-summary", action="store_true",
                   help="wait until a summary exists before exporting a recording")
    s.add_argument("--mark-existing", action="store_true",
                   help="mark every current recording as done without creating docs")
    sub.add_parser("status", help="show export history")
    i = sub.add_parser("inspect", help="dump raw Plaud data (no ID = list recent)")
    i.add_argument("recording_id", nargs="?")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    handlers = {"login": cmd_login, "auth-google": cmd_auth_google, "sync": cmd_sync,
                "status": cmd_status, "inspect": cmd_inspect}
    result = handlers[args.cmd](cfg, args)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
