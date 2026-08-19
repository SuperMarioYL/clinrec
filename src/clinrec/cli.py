"""``clinrec`` — the on-prem clinical-timeline CLI.

Commands:
  clinrec init                    detect Ollama, write ./clinrec.toml
  clinrec ingest <folder>         OCR + NER + link + dedup + assemble
  clinrec timeline                Rich TUI: browse events + audit chain
  clinrec audit --export <path>  emit regulator-reviewable audit.jsonl
  clinrec eval                    killer-falsifier quality harness
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .audit import AuditChain
from .dedup import Deduplicator
from .ingest import ingest_folder
from .llm import DEFAULT_HOST, DEFAULT_MODEL, Linker
from .resolve import EntityExtractor
from .store import State
from .timeline import TimelineAssembler
from .tui import run_timeline_tui
from .eval import run_eval

app = typer.Typer(
    name="clinrec",
    help="On-prem primitive turning faxed/scanned PHI into a regulator-reviewable clinical timeline.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
log = logging.getLogger("clinrec")

CONFIG_DEFAULTS = {
    "ollama_host": DEFAULT_HOST,
    "ollama_model": DEFAULT_MODEL,
    "state_dir": ".clinrec",
}


def _write_config(path: Path, host: str, model: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# clinrec local config — operator-managed, never shipped.\n"
        "# PHI never leaves this host; ollama runs on-box.\n"
        f'ollama_host = "{host}"\n'
        f'ollama_model = "{model}"\n'
        'state_dir = ".clinrec"\n'
    )
    path.write_text(body, encoding="utf-8")
    return path


def _read_config(path: Path) -> dict:
    if not path.exists():
        return dict(CONFIG_DEFAULTS)
    cfg = dict(CONFIG_DEFAULTS)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _ingest_extractor_id(mime: str) -> str:
    """Map a record mime to the on-prem extractor that produced its text.

    Mirrors ingest.extract_text: pdfplumber for PDF, tesseract OCR for
    images, striprtf for .rtf, and path.read_text for everything else
    (.txt/.md). The audit docstring records the model id that produced the
    record, so a false id (e.g. 'text' for an .rtf whose output_sha256 is the
    sha of the striprtf-cleaned text) would mislead a regulator replaying it.

    v0.6.0 — fix-rtf-ingest-audit-wrong-extractor-id: the v0.3.0 RTF fix
    added _read_rtf (striprtf) but left .rtf falling through to 'text', so
    the audit recorded the wrong extractor and a regulator replaying with
    path.read_text on raw RTF markup could not match the recorded
    output_sha256. .rtf now maps to 'striprtf' to match ingest.extract_text.
    """
    if mime.startswith("image"):
        return "ocr+tesseract"
    if mime == "application/pdf":
        return "pdfplumber"
    if mime == "application/rtf":
        return "striprtf"
    return "text"


@app.command()
def init(
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Ollama daemon URL."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Ollama model tag."),
) -> None:
    """Detect Ollama, validate the clinical NER engine, write ./clinrec.toml."""
    cfg_path = Path("clinrec.toml")
    _write_config(cfg_path, host, model)
    console.print(f"[green]wrote[/green] {cfg_path}")

    # Probe Ollama (graceful — the linker falls back to rule-based if down).
    linker = Linker(model=model, host=host)
    if linker.is_available():
        console.print(f"[green]Ollama reachable[/green] at {host} (model tag: {model})")
        console.print(
            "  pull the model if not present: [cyan]ollama pull {0}[/cyan]".format(model)
        )
    else:
        console.print(
            f"[yellow]Ollama not reachable[/yellow] at {host} — "
            "clinrec will use the rule-based coder (degraded but functional)."
        )
        console.print("  start it with [cyan]ollama serve[/cyan] then [cyan]ollama pull {0}[/cyan]".format(model))

    # Validate the clinical NER engine (medspaCy vs fallback).
    extr = EntityExtractor()
    if extr.uses_medspacy:
        console.print("[green]medspaCy[/green] clinical NER pipeline loaded.")
    else:
        console.print(
            "[yellow]medspaCy not available[/yellow] — using the regex NER fallback. "
            "Install medspacy for production clinical NER."
        )
    console.print(
        Panel.fit(
            "[dim]Next: [cyan]clinrec ingest ./sample-records[/cyan] "
            "to OCR + NER + link + assemble a timeline.[/dim]",
            border_style="dim",
        )
    )


@app.command()
def ingest(
    folder: Path = typer.Argument(..., help="Folder of faxed/scanned records to ingest."),
    patient: str = typer.Option("patient-local", "--patient", help="Local patient pseudonym."),
) -> None:
    """OCR + content-hash dedup + medspaCy NER + on-prem link + timeline assemble."""
    cfg = _read_config(Path("clinrec.toml"))
    linker = Linker(model=cfg.get("ollama_model", DEFAULT_MODEL), host=cfg.get("ollama_host", DEFAULT_HOST))
    extractor = EntityExtractor()
    audit = AuditChain()
    dedup = Deduplicator()

    console.rule(f"[bold]Ingesting[/bold] {folder}")
    records, dedup = ingest_folder(folder, dedup=dedup)
    if not records:
        console.print("[yellow]No records ingested (folder empty or all duplicates).[/yellow]")
        raise typer.Exit(code=1)

    for rec in records:
        # v0.5.0 — fix-ingest-audit-output-hash-mismatch: bind the byte-level
        # file fingerprint as input and the raw extracted-text hash (the actual
        # NER input, rec.ocr_text) as output, so a regulator replaying the chain
        # matches the ingest output to the NER input. Previously both were the
        # lossy normalized dedup hash (content_sha256), which NER never consumed.
        audit.record(
            op="ingest",
            input_sha256=rec.file_sha256,
            llm_model_id=_ingest_extractor_id(rec.mime),
            output_sha256=rec.raw_text_sha256,
        )

    assembler = TimelineAssembler(extractor=extractor, linker=linker, audit=audit)
    timeline = assembler.assemble(records, patient_pseudonym=patient)

    state = State(cfg.get("state_dir", ".clinrec"))
    state_path = state.save(timeline, dedup, config=cfg)

    summary = Table(title="Ingest summary", show_header=False)
    summary.add_column("k", style="cyan")
    summary.add_column("v", overflow="fold")
    summary.add_row("records", str(len(records)))
    summary.add_row("duplicates skipped", str(dedup.duplicates_seen))
    summary.add_row("entities (coded)", str(len(timeline.entities)))
    summary.add_row("timeline events (de-dup)", str(len(timeline.events)))
    summary.add_row("audit entries", str(len(timeline.audit_chain)))
    summary.add_row("phi_egress", "[green]False[/green] (primitive invariant)")
    summary.add_row("NER engine", "medspaCy" if extractor.uses_medspacy else "regex fallback")
    summary.add_row("linker engine", "ollama" if linker.is_available() else "rule-based (ollama down)")
    summary.add_row("state", str(state_path))
    console.print(summary)
    console.print(
        f"[dim]Browse: [cyan]clinrec timeline[/cyan]  ·  Export audit: "
        f"[cyan]clinrec audit --export audit.jsonl[/cyan][/dim]"
    )


@app.command()
def timeline(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the de-duplicated timeline events as a JSON array on stdout instead of the TUI."
    ),
) -> None:
    """Browse the de-duplicated timeline (Rich TUI), or emit events as JSON."""
    state = State()
    if not state.exists():
        console.print("[yellow]No state found. Run [cyan]clinrec ingest <folder>[/cyan] first.[/yellow]")
        raise typer.Exit(code=1)
    tl, _dedup, _cfg = state.load()
    if json_out:
        # v0.4.0 feat-timeline-json-export: machine-readable resolved timeline
        # for a regulator/CI/agent consumer (the TUI is human-only; audit
        # --export covers the audit chain, not the events themselves).
        typer.echo(json.dumps([e.model_dump(mode="json") for e in tl.events], indent=2, default=str))
        return
    run_timeline_tui(tl, console=console)


@app.command()
def audit(
    export: Optional[Path] = typer.Option(
        None, "--export", help="Write the regulator-reviewable audit log to this path (JSONL)."
    ),
    show: bool = typer.Option(False, "--show", help="Print the audit chain to stdout."),
    verify: Optional[Path] = typer.Option(
        None,
        "--verify",
        help="Independently verify a saved audit chain (state.json path); exit 0 (PASS) / 1 (FAIL + diagnostic).",
    ),
) -> None:
    """Inspect, export, or independently verify the regulator-reviewable audit chain."""
    # v0.5.0 — feature-audit-verify-cli: a regulator or CI workflow can
    # independently attest a saved chain's integrity from the CLI without
    # writing Python. Pairs with the chain-hash fix (the digest now covers
    # prompt_sha256 + ts); delegates to AuditChain.first_broken_link so the
    # digest formula lives in audit.py and the command stays thin.
    #
    # v0.6.0 — fix-audit-verify-omits-phi-egress-invariant: the linked chain
    # hash deliberately excludes phi_egress, so first_broken_link() alone
    # could not detect a flipped phi_egress=True and --verify printed PASS
    # exit 0 for a chain that records a PHI-egress event — hiding the
    # primitive's headline compliance guarantee. The --verify path now also
    # calls verify_invariant(), mirroring the --export path's dual
    # (chain + invariant) attestation.
    if verify is not None:
        if not verify.exists():
            console.print(f"[red]state file not found:[/red] {verify}")
            raise typer.Exit(code=1)
        try:
            data = json.loads(verify.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]could not read state:[/red] {verify} ({exc})")
            raise typer.Exit(code=1)
        rows = data.get("audit_chain", []) if isinstance(data, dict) else []
        chain = AuditChain.from_state(rows)
        broken = chain.first_broken_link()
        if broken is not None:
            idx, entry = broken
            console.print(
                f"[red]FAIL[/red] — chain broken at index {idx} "
                f"(op={entry.op}, audit_id={entry.audit_id})."
            )
            raise typer.Exit(code=1)
        if not chain.verify_invariant():
            egress = next(e for e in chain if e.phi_egress)
            console.print(
                f"[red]FAIL[/red] — phi_egress invariant violated at "
                f"op={egress.op}, audit_id={egress.audit_id} "
                f"(phi_egress=True). PHI-egress event recorded in the chain."
            )
            raise typer.Exit(code=1)
        console.print(
            f"[green]PASS[/green] — audit chain integrity verified "
            f"({len(chain)} entries); phi_egress invariant holds."
        )
        raise typer.Exit(code=0)

    state = State()
    if not state.exists():
        console.print("[yellow]No state found. Run [cyan]clinrec ingest <folder>[/cyan] first.[/yellow]")
        raise typer.Exit(code=1)
    tl, _dedup, _cfg = state.load()
    chain = AuditChain.from_state([e.model_dump(mode="json") for e in tl.audit_chain])

    if show or export is None:
        from .tui import _audit_table

        console.print(_audit_table(tl))
    if export is not None:
        out = chain.export(export)
        console.print(f"[green]exported[/green] {len(chain)} audit entries → {out}")
        invariant = chain.verify_invariant()
        console.print(
            f"[green]phi_egress invariant[/green]: "
            f"{'PASS — no PHI ever left the host' if invariant else '[red]FAIL — investigate[/red]'}"
        )
        chain_ok = chain.verify_chain_integrity()
        console.print(
            f"[green]audit chain integrity[/green]: "
            f"{'PASS — linked hash chain unbroken' if chain_ok else '[red]FAIL — chain tampered[/red]'}"
        )


@app.command()
def eval(
    gold: Optional[Path] = typer.Option(
        None, "--gold", help="Path to a gold JSONL file (defaults to the embedded set)."
    ),
) -> None:
    """Run the killer-falsifier: on-prem NER+linker vs medspaCy-only baseline."""
    from .eval import load_gold

    gold_set = load_gold(gold) if gold is not None else None
    report = run_eval(gold=gold_set)
    console.print(Panel.fit(report.to_markdown(), title="ClinRec Eval", border_style="cyan"))
    if report.linker_metrics.f1 >= report.baseline_metrics.f1:
        console.print("[green]PASS[/green] — on-prem linker clears the medspaCy-only baseline.")
    else:
        console.print("[red]FAIL[/red] — on-prem linker does NOT clear the baseline bar.")


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """On-prem clinical-timeline primitive. PHI never leaves the host."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def version() -> None:
    """Print the clinrec version."""
    console.print(f"clinrec {__version__}")


if __name__ == "__main__":
    app()
