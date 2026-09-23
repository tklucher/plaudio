"""The core job: find recordings we haven't exported yet and make a Doc for each."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import render, summarize
from .plaud import PlaudError, Recording
from .state import State

log = logging.getLogger("plaudio")


@dataclass
class SyncOptions:
    dry_run: bool = False
    full: bool = False                 # ignore lookback window, scan every recording
    lookback_days: int = 14            # re-scan window, catches late device syncs
    limit: int | None = None           # max docs to create this run
    require_summary: bool = False      # wait for a summary before exporting
    mark_only: bool = False            # record everything as done without creating docs


@dataclass
class SyncReport:
    created: list[tuple[Recording, str]] = field(default_factory=list)
    skipped_not_ready: list[Recording] = field(default_factory=list)
    already_in_drive: list[Recording] = field(default_factory=list)
    failed: list[tuple[Recording, str]] = field(default_factory=list)
    marked: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


async def run_sync(plaud: Any, writer: Any, state: State, folder_name: str,
                   opts: SyncOptions, echo: Callable[[str], None] = print) -> SyncReport:
    started = datetime.now(timezone.utc)
    report = SyncReport()

    date_from = None
    if state.last_run and not opts.full:
        date_from = (state.last_run - timedelta(days=opts.lookback_days)).date()

    recordings = await plaud.list_recordings(date_from=date_from)
    todo = [r for r in recordings if not state.is_processed(r.id)]
    todo.sort(key=lambda r: r.when or "")
    echo(f"Plaud: {len(recordings)} recording(s) scanned"
         f"{f' since {date_from}' if date_from else ''}, {len(todo)} new.")

    if opts.mark_only:
        for rec in todo:
            if not opts.dry_run:
                state.mark_processed(rec.id, None, rec.name)
            report.marked += 1
        if not opts.dry_run:
            state.touch_last_run(started)
        echo(f"Marked {report.marked} recording(s) as already handled.")
        return report

    if opts.limit is not None:
        todo = todo[: opts.limit]

    folder_id = None
    if not opts.dry_run and todo:
        folder_id = writer.ensure_folder(state.folder_id, folder_name)
        if folder_id != state.folder_id:
            state.folder_id = folder_id
            state.save()

    for rec in todo:
        try:
            transcript = await plaud.get_transcript(rec.id)
            if not render.transcript_segments(transcript):
                echo(f"  … {rec.name}: transcript not ready yet, will retry next run")
                report.skipped_not_ready.append(rec)
                continue

            try:
                note = await plaud.get_note(rec.id)
            except PlaudError as exc:
                log.debug("get_note failed for %s: %s", rec.id, exc)
                note = None
            summary_md = render.summary_markdown(note)
            source = "Plaud"
            if not summary_md and summarize.available():
                summary_md = summarize.summarize(render.transcript_plain_text(transcript))
                source = "Claude"
            if not summary_md and opts.require_summary:
                echo(f"  … {rec.name}: no summary yet, will retry next run")
                report.skipped_not_ready.append(rec)
                continue

            title = render.doc_title(rec)
            if opts.dry_run:
                echo(f"  [dry-run] would create: {title}")
                report.created.append((rec, "dry-run"))
                continue

            existing = writer.find_existing(folder_id, rec.id)
            if existing:
                state.mark_processed(rec.id, existing, rec.name)
                report.already_in_drive.append(rec)
                echo(f"  = {title} (already in Drive)")
                continue

            html_doc = render.build_doc_html(rec, summary_md, transcript, source)
            doc_id = writer.create_doc(folder_id, title, html_doc, rec.id)
            state.mark_processed(rec.id, doc_id, rec.name)
            report.created.append((rec, doc_id))
            echo(f"  + {title}  https://docs.google.com/document/d/{doc_id}")
        except Exception as exc:  # keep going; this one is retried next run
            log.debug("failed on %s", rec.id, exc_info=True)
            report.failed.append((rec, str(exc)))
            echo(f"  ! {rec.name}: {exc}")

    if not opts.dry_run:
        state.touch_last_run(started)
    echo(f"Done: {len(report.created)} created, {len(report.skipped_not_ready)} not ready, "
         f"{len(report.already_in_drive)} already present, {len(report.failed)} failed.")
    return report
