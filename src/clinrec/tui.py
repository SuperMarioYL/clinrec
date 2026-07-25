"""Rich timeline TUI (m2) — browse de-duplicated events + drill into evidence.

A prompt-driven Rich browser (no full-screen curses dependency): list
events in a table, pick one to see its evidence spans and the audit-chain
entry that assembled it, or dump the full audit chain. Keeps the install
lightweight (``rich`` only) and the surface scriptable.
"""
from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from .models import ClinicalTimeline, TimelineEvent


def _status_color(status: str) -> str:
    return {
        "active": "green",
        "resolved": "cyan",
        "negated": "yellow",
        "unknown": "dim",
    }.get(status, "white")


def _fmt_date(d) -> str:
    return d.isoformat() if d else "—"


def _events_table(timeline: ClinicalTimeline) -> Table:
    table = Table(
        title=f"Clinical Timeline — {timeline.patient_pseudonym}",
        caption=f"{len(timeline.events)} de-duplicated events across "
        f"{len(timeline.records)} record(s)",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Date", width=12)
    table.add_column("Type", width=12)
    table.add_column("Code", width=14)
    table.add_column("System", width=10)
    table.add_column("Status")
    table.add_column("Evidence", overflow="fold")
    for i, ev in enumerate(timeline.events, 1):
        ev_span = ev.evidence_spans[0].text_span if ev.evidence_spans else ""
        more = f"  (+{len(ev.evidence_spans) - 1} more)" if len(ev.evidence_spans) > 1 else ""
        table.add_row(
            str(i),
            _fmt_date(ev.onset_date),
            ev.entity_type.value,
            ev.normalized_code or "—",
            ev.code_sys.value,
            f"[{_status_color(ev.status.value)}]{ev.status.value}[/{_status_color(ev.status.value)}]",
            f"{ev_span}{more}",
        )
    return table


def _event_detail(ev: TimelineEvent) -> Table:
    table = Table(title=f"Event {ev.event_id}", show_header=False)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("Type", ev.entity_type.value)
    table.add_row("Code", ev.normalized_code or "—")
    table.add_row("System", ev.code_sys.value)
    table.add_row("Onset", _fmt_date(ev.onset_date))
    table.add_row("Status", ev.status.value)
    table.add_row("Audit id", ev.audit_id)
    table.add_row("Entity ids", ", ".join(ev.entity_ids) or "—")
    table.add_row("Evidence spans", "")
    for sp in ev.evidence_spans:
        neg = "[negated] " if sp.is_negated else ""
        table.add_row(
            "",
            f"{neg}\"{sp.text_span}\"  (record {sp.source_record_id}, "
            f"offset {sp.start}-{sp.end})",
        )
    return table


def _audit_table(timeline: ClinicalTimeline) -> Table:
    table = Table(title="Audit Chain (regulator-reviewable, no PHI egress)")
    table.add_column("#", style="dim", width=4)
    table.add_column("op")
    table.add_column("input_sha256", overflow="fold", max_width=18)
    table.add_column("llm_model_id")
    table.add_column("output_sha256", overflow="fold", max_width=18)
    table.add_column("phi_egress")
    for i, e in enumerate(timeline.audit_chain, 1):
        egress = "[red]True[/red]" if e.phi_egress else "[green]False[/green]"
        table.add_row(
            str(i),
            e.op,
            e.input_sha256[:16] + "…",
            e.llm_model_id,
            e.output_sha256[:16] + "…" if e.output_sha256 else "—",
            egress,
        )
    return table


def run_timeline_tui(
    timeline: ClinicalTimeline,
    console: Optional[Console] = None,
    input_stream=None,
) -> None:
    """Interactive browser. ``input_stream`` is for scripted/testing input."""
    console = console or Console()
    if not timeline.events:
        console.print("[yellow]No timeline events to browse.[/yellow]")
        console.print(
            f"Records ingested: {len(timeline.records)}. "
            "Run `clinrec ingest <folder>` on records with clinical content first."
        )
        return

    while True:
        console.rule("[bold]ClinRec Timeline[/bold]")
        console.print(_events_table(timeline))
        console.print(
            "\n[dim]Enter an event # to drill in, 'a' for the full audit chain, 'q' to quit.[/dim]"
        )
        choice = Prompt.ask(
            "Action",
            console=console,
            stream=input_stream,
            default="q",
        ).strip().lower()
        if choice in ("q", "quit", "exit"):
            console.print("[dim]exiting timeline browser[/dim]")
            return
        if choice in ("a", "audit"):
            console.print(_audit_table(timeline))
            Prompt.ask("[dim]press Enter to continue[/dim]", console=console, stream=input_stream, default="")
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(timeline.events):
            ev = timeline.events[int(choice) - 1]
            console.print(_event_detail(ev))
            Prompt.ask("[dim]press Enter to continue[/dim]", console=console, stream=input_stream, default="")
            continue
        console.print("[yellow]invalid choice[/yellow]")


__all__ = ["run_timeline_tui"]
