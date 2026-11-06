"""Command line: `trackcard analyze` and `trackcard mcp`."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from . import __version__
from .audio import TrackcardError


@click.group()
@click.version_option(__version__, prog_name="trackcard")
def main() -> None:
    """trackcard: listens to a WAV bounce and writes a track card an AI can read."""


@main.command()
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--json", "json_too", is_flag=True, help="Also write <name>.trackcard.json.")
@click.option("--out", "out_dir", type=click.Path(path_type=Path, file_okay=False), default=None,
              help="Folder for the card (default: next to the WAV).")
@click.option("--no-stems", is_flag=True, help="Skip Demucs stem separation (much faster, less detail).")
@click.option("--sections", "sections_mode", type=click.Choice(["auto", "allin1", "laplacian"]), default="auto",
              show_default=True, help="Section method. auto = allin1 if installed, else Laplacian.")
@click.option("--print", "print_card", is_flag=True, help="Also print the card to stdout.")
@click.option("--bpm", type=float, default=None, help="Project tempo, if you know it. Overrides the measurement.")
@click.option("--key", "key", default=None, help="Key, if you know it: 'D minor', 'Dm', 'F# major', 'Bb' or '7A'.")
def analyze(path: Path, json_too: bool, out_dir: Path | None, no_stems: bool, sections_mode: str, print_card: bool,
            bpm: float | None, key: str | None) -> None:
    """Analyse PATH (a WAV bounce) and write <name>.trackcard.md."""
    from .card import to_markdown, write_card
    from .pipeline import analyze as run

    t0 = time.time()

    def progress(msg: str) -> None:
        click.echo(f"[{time.time() - t0:5.0f}s] {msg}...", err=True)

    try:
        card = run(path, sections_mode=sections_mode, stems=not no_stems, progress=progress, bpm=bpm, key=key)
        written = write_card(card, Path(path), out_dir, json_too)
    except TrackcardError as exc:
        click.echo(f"trackcard: error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:  # never fail silently
        click.echo(f"trackcard: unexpected error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)
    for n in card.notes:
        click.echo(f"note: {n}", err=True)
    for p in written:
        click.echo(f"wrote {p}", err=True)
    if print_card:
        click.echo(to_markdown(card))


@main.command()
def mcp() -> None:
    """Run the MCP server on stdio (one tool: analyze_track)."""
    from .mcp_server import run

    run()


if __name__ == "__main__":
    main()
