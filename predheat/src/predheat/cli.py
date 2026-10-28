"""Command line entry points: ``predheat <command>``."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import DataMode, get_settings
from .service import build_platform

app = typer.Typer(help="Heat Pump Pilot: heat pump and home energy optimiser (read-only PoC).", no_args_is_help=True)
console = Console()


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False):
    """Run the API (and background collector)."""
    import uvicorn

    s = get_settings()
    uvicorn.run("predheat.api.app:app", host=host or s.api_host, port=port or s.api_port, reload=reload)


@app.command()
def discover(out: Path = Path("docs/discovery/latest.json"), markdown: bool = True):
    """Run capability discovery against the configured data source and save the report."""
    p = build_platform()

    async def _run():
        report = await p.telemetry.discover()
        return report

    report = asyncio.run(_run())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2))
    table = Table(title=f"Capability report ({report.provenance.value})")
    table.add_column("Signal")
    table.add_column("Status")
    table.add_column("Quality")
    table.add_column("Source")
    table.add_column("Conf")
    for c in report.capabilities:
        table.add_row(c.signal.value, c.status, c.quality.value, c.source_ref or "", f"{c.confidence:.2f}")
    console.print(table)
    for w in report.warnings:
        console.print(f"[yellow]warning:[/] {w}")
    missing = report.missing_for_thermal_model()
    if missing:
        console.print(f"[red]Missing for thermal model:[/] {', '.join(m.value for m in missing)}")
    else:
        console.print("[green]All minimum signals for thermal modelling are available.[/]")
    if markdown:
        md = out.with_suffix(".md")
        lines = [f"# Capability report ({report.generated_at.isoformat()})", "", "| Signal | Status | Quality | Source | Confidence | Notes |", "|---|---|---|---|---|---|"]
        for c in report.capabilities:
            lines.append(f"| {c.signal.value} | {c.status} | {c.quality.value} | `{c.source_ref or ''}` | {c.confidence:.2f} | {c.notes} |")
        lines += ["", "## Writable controls (not used)", ""]
        for w in report.writable_controls:
            lines.append(f"- `{w['entity_id']}` ({w['domain']}) state={w['state']} min={w.get('min')} max={w.get('max')} step={w.get('step')}")
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in report.warnings]
        md.write_text("\n".join(lines) + "\n")
    console.print(f"Saved {out}")


@app.command()
def bootstrap(backfill_days: int | None = None):
    """Initialise the database, discover, backfill history and poll once."""
    p = build_platform()
    asyncio.run(p.bootstrap(backfill_days))
    console.print(json.dumps(p.last_poll, indent=2, default=str))


@app.command()
def costs(days: int = 7):
    """Print the energy cost summary for the last N days."""
    p = build_platform()
    asyncio.run(p.bootstrap(0 if get_settings().data_mode == DataMode.HOME_ASSISTANT else None))
    today = p.clock.now().date()
    summary = p.energy_costs(today - timedelta(days=days), today + timedelta(days=1))
    console.print_json(json.dumps(summary, default=str))


@app.command()
def fixtures(out: Path = Path("tests/fixtures/simulated"), days: int = 7, seed: int = 7):
    """Regenerate the simulated fixture dataset."""
    from .integrations.simulated import SimConfig, SimulatedHome

    home = SimulatedHome(SimConfig(seed=seed, days=days))
    out.mkdir(parents=True, exist_ok=True)
    rows = [{**r, "ts": r["ts"].isoformat()} for r in home.records[:: 6]]  # 30-minute thinning
    (out / "home_records_30min.json").write_text(json.dumps(rows))
    (out / "ground_truth.json").write_text(json.dumps(home.ground_truth(), indent=2))
    console.print(f"Wrote {len(rows)} records to {out}")


if __name__ == "__main__":  # pragma: no cover
    app()
