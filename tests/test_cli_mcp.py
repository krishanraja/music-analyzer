import asyncio

import soundfile as sf
from click.testing import CliRunner

from synth import SR, three_part_track
from trackcard.cli import main


def _wav(tmp_path):
    y, _ = three_part_track()
    p = tmp_path / "aba.wav"
    sf.write(p, y, SR)
    return p


def test_cli_writes_card_and_json(tmp_path):
    p = _wav(tmp_path)
    r = CliRunner().invoke(main, ["analyze", str(p), "--json", "--no-stems"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "aba.trackcard.md").exists()
    assert (tmp_path / "aba.trackcard.json").exists()
    assert "wrote" in r.output and "note:" in r.output  # fallbacks are printed, not hidden


def test_cli_clear_error_on_missing_file(tmp_path):
    r = CliRunner().invoke(main, ["analyze", str(tmp_path / "missing.wav")])
    assert r.exit_code == 1
    assert "trackcard: error: File not found" in r.output


def test_mcp_lists_one_tool_and_returns_card(tmp_path):
    from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

    from trackcard.mcp_server import server

    p = _wav(tmp_path)

    async def go():
        lt = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        tools = lt.root.tools
        ct = await server.request_handlers[CallToolRequest](
            CallToolRequest(method="tools/call", params=CallToolRequestParams(
                name="analyze_track", arguments={"path": str(p), "stems": False, "save": False}))
        )
        return tools, ct.root.content[0].text

    tools, text = asyncio.run(go())
    assert [t.name for t in tools] == ["analyze_track"]
    assert text.startswith("# Track card: aba.wav") and "## 6. Splice brief" in text


def test_mcp_error_is_reported_as_text(tmp_path):
    from trackcard.mcp_server import call_tool

    out = asyncio.run(call_tool("analyze_track", {"path": str(tmp_path / "nope.wav")}))
    assert out[0].text.startswith("Error: File not found")


def test_cli_overrides_show_in_card_and_json(tmp_path):
    import json

    p = _wav(tmp_path)
    r = CliRunner().invoke(main, ["analyze", str(p), "--no-stems", "--json", "--bpm", "124", "--key", "Dm"])
    assert r.exit_code == 0, r.output
    md = (tmp_path / "aba.trackcard.md").read_text()
    assert "**124** (set by the producer; trackcard measured 124" in md
    assert "**D minor**, Camelot **7A** (set by the producer; trackcard measured A minor (8A))" in md
    d = json.loads((tmp_path / "aba.trackcard.json").read_text())
    assert d["bpm_source"] == "producer" and d["key_source"] == "producer" and d["camelot"] == "7A"


def test_cli_rejects_bad_key_and_bpm(tmp_path):
    p = _wav(tmp_path)
    r = CliRunner().invoke(main, ["analyze", str(p), "--no-stems", "--key", "Dorian"])
    assert r.exit_code == 1 and "Use forms like" in r.output
    r = CliRunner().invoke(main, ["analyze", str(p), "--no-stems", "--bpm", "900"])
    assert r.exit_code == 1 and "out of range" in r.output


def test_mcp_accepts_overrides(tmp_path):
    from trackcard.mcp_server import call_tool

    p = _wav(tmp_path)
    out = asyncio.run(call_tool("analyze_track", {"path": str(p), "stems": False, "save": False, "bpm": 124, "key": "7A"}))
    assert "set by the producer" in out[0].text and "D minor" in out[0].text
