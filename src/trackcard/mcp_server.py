"""Minimal MCP server exposing one tool, analyze_track, over stdio.

Server skeleton (low-level mcp.server.Server with list_tools / call_tool on
stdio) follows the pattern used by Stem MCP (MIT); see NOTICE.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .audio import TrackcardError

log = logging.getLogger("trackcard.mcp")
server = Server("trackcard")

TOOL = Tool(
    name="analyze_track",
    description=(
        "Listen to a local WAV bounce and return its track card: BPM, key and Camelot code, "
        "section map with bar numbers, stem activity per section, frequency balance, loudness, "
        "and a Splice brief of sounds that would fill measured gaps. Runs locally; stem "
        "separation takes roughly 1-2x the track length on CPU."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to a WAV file on this computer."},
            "stems": {"type": "boolean", "default": True, "description": "Run Demucs stem separation (slower, more detail)."},
            "sections": {"type": "string", "enum": ["auto", "allin1", "laplacian"], "default": "auto"},
            "bpm": {"type": "number", "description": "Project tempo, if the producer gave it. Overrides the measurement."},
            "key": {"type": "string", "description": "Key, if the producer gave it, e.g. 'D minor', 'Dm', 'F# major' or '7A'."},
            "include_json": {"type": "boolean", "default": False, "description": "Append the card as JSON too."},
            "save": {"type": "boolean", "default": True, "description": "Also write <name>.trackcard.md next to the WAV."},
        },
        "required": ["path"],
    },
)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [TOOL]


def analyze_track(args: dict[str, Any]) -> str:
    from .card import to_json, to_markdown, write_card
    from .pipeline import analyze

    path = args.get("path")
    if not path:
        raise TrackcardError("analyze_track needs a 'path' to a WAV file.")
    card = analyze(path, sections_mode=args.get("sections", "auto"), stems=args.get("stems", True),
                   bpm=args.get("bpm"), key=args.get("key"))
    text = to_markdown(card)
    if args.get("save", True):
        write_card(card, Path(path), None, args.get("include_json", False))
    if args.get("include_json"):
        text += "\n\n```json\n" + to_json(card) + "\n```\n"
    return text


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != TOOL.name:
        return [TextContent(type="text", text=f"Error: unknown tool '{name}'. The only tool is analyze_track.")]
    try:
        text = await asyncio.to_thread(analyze_track, arguments or {})
    except TrackcardError as exc:
        text = f"Error: {exc}"
    except Exception as exc:
        log.exception("analyze_track failed")
        text = f"Error: unexpected {type(exc).__name__}: {exc}"
    return [TextContent(type="text", text=text)]


async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
