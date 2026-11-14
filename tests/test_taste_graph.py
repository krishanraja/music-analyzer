"""The playlist graph builder and the chronology: added dates, month/year edges, labelled fallbacks, periods."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

GRAPH = Path(__file__).resolve().parents[1] / "taste" / "graph"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, GRAPH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def card(duration: float, key: str = "A minor", camelot: str = "8A", bpm: float = 122.0, stems: bool = False) -> dict:
    sec = lambda s, e, energy, bar: {"start": s, "end": e, "start_bar": bar, "energy": energy,
                                     "bands_rel_db": {"sub": -4, "low": 0, "mid": -5, "high-mid": -13, "air": -19},
                                     **({"stems": {k: {"density": 0.5} for k in ("drums", "bass", "vocals", "other")}} if stems else {})}
    third = duration / 3
    return {"duration": duration, "bpm": bpm, "likely_project_bpm": bpm, "bpm_confidence": "high", "key": key,
            "camelot": camelot, "key_confidence": "high", "key_alternative": None,
            "loudness": {"integrated_lufs": -10.0, "crest_db": 11.5},
            "sections": [sec(0, third, "mid", 1), sec(third, 2 * third, "peak", 9), sec(2 * third, duration, "low", 17)],
            "stems_available": stems, "drops": [{"time": third}] if stems else []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    bg = load("build_graph")
    here = tmp_path / "graph"
    here.mkdir()
    monkeypatch.setattr(bg, "HERE", here)
    tracks = [
        {"id": "AAA", "title": "Early", "artists": ["One"], "duration_s": 300, "release_date": "2013-05-01",
         "label": "℗ 2019, 1983 Some Label", "label_source": "Spotify album page", "added_at": "2014-05-02T10:00:00Z"},
        {"id": "BBB", "title": "Middle", "artists": ["two"], "duration_s": 400, "release_date": "2020",
         "label": "Other", "label_source": "Spotify album page", "added_at": "2014-05-20T10:00:00Z"},
        {"id": "CCC", "title": "Late", "artists": ["Three"], "duration_s": 360, "release_date": "2025-01-01",
         "label": None, "label_source": None, "added_at": "2026-11-07T19:03:08Z"},
        {"id": "DDD", "title": "Preview only", "artists": ["One"], "duration_s": 250, "release_date": "2015-01-01",
         "label": "L", "label_source": "x", "added_at": "2026-02-01T00:00:00Z"},
    ]
    (here / "playlist.json").write_text(json.dumps({"read_at": "2026-11-13T00:00:00Z", "tracks": tracks}), encoding="utf-8")
    (here / "families.json").write_text(json.dumps({"families": {
        "fam_a": {"name": "A", "description": "a", "artists": ["One", "Two"]},
        "fam_b": {"name": "B", "description": "b", "artists": ["Three"]}}}), encoding="utf-8")
    cards = tmp_path / "full"
    cards.mkdir()
    (cards / "AAA.trackcard.json").write_text(json.dumps(card(301.0)), encoding="utf-8")
    (cards / "AAA.stems.trackcard.json").write_text(json.dumps(card(301.0, stems=True)), encoding="utf-8")
    (cards / "BBB.trackcard.json").write_text(json.dumps(card(398.0, "C major", "8B", 120)), encoding="utf-8")
    (cards / "fetch_log.jsonl").write_text(json.dumps({"id": "AAA", "ok": True, "source": "soundcloud", "url": "u",
                                                       "upload_title": "t", "diff_s": 1.0}) + "\n", encoding="utf-8")
    prev = tmp_path / "previews"
    prev.mkdir()
    (prev / "DDD.trackcard.json").write_text(json.dumps(card(30.0)), encoding="utf-8")
    graph = bg.main(cards, prev)
    rows = {r["id"]: r for r in csv.DictReader(open(here / "tracks.csv", newline="", encoding="utf-8"))}
    return bg, graph, rows, here


def test_added_dates_on_nodes_rows_and_edges(world):
    _, graph, rows, _ = world
    track = next(n for n in graph["nodes"] if n["id"] == "track:CCC")
    assert track["added_at"] == "2026-11-07T19:03:08Z"
    assert rows["CCC"]["added_at"] == "2026-11-07T19:03:08Z" and rows["CCC"]["added_quarter"] == "2026-Q4"
    added = {(e["source"], e["target"]) for e in graph["edges"] if e["rel"] == "added_in"}
    assert ("track:CCC", "added_month:2026-11") in added and ("track:CCC", "added_year:2026") in added
    in_year = [e for e in graph["edges"] if e["rel"] == "in_year" and e["source"] == "added_month:2014-05"]
    assert len(in_year) == 1  # two tracks added that month, one month->year edge


def test_full_preview_and_none_are_labelled(world):
    _, graph, rows, _ = world
    assert rows["AAA"]["measured_from"] == "full" and rows["AAA"]["full_track"] == "True"
    assert rows["DDD"]["measured_from"] == "preview" and rows["DDD"]["full_track"] == "False"
    assert rows["CCC"]["measured_from"] == "none" and rows["CCC"]["measured"] == "False"
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert "SoundCloud" in nodes["track:AAA"]["measured_from"] and "stems" in nodes["track:AAA"]["measured_from"]
    assert "preview" in nodes["track:DDD"]["measured_from"]


def test_stems_card_adds_drops_but_not_to_the_distance(world):
    _, graph, rows, _ = world
    assert rows["AAA"]["drops"] == "1" and rows["BBB"]["drops"] == ""
    basis = {e["basis"] for e in graph["edges"] if e["rel"] == "sounds_like"}
    assert basis and all("stems" not in b for b in basis)  # BBB has no stems, so stems stay out of the vector


def test_family_match_is_case_insensitive_and_label_is_cleaned(world):
    _, _, rows, _ = world
    assert rows["BBB"]["primary_family"] == "fam_a"  # artist "two" vs "Two" in families.json
    assert rows["AAA"]["label"] == "Some Label"


def test_chronology_periods_merge_thin_years(world, monkeypatch):
    ch = load("chronology")
    rows = [{"added_year": y, "measured_from": "full"} for y, n in (("2014", 12), ("2019", 4), ("2020", 7), ("2021", 3))
            for _ in range(n)]
    names = [name for name, _ in ch.periods(rows)]
    assert names == ["2014", "2019-21"]  # 2019+2020 reach 11; the 3 left in 2021 join the last period


def test_chronology_end_to_end(world, monkeypatch):
    _, _, _, here = world
    ch = load("chronology")
    monkeypatch.setattr(ch, "HERE", here)
    monkeypatch.setattr(ch, "MIN_N", 1)
    (here / "CHRONOLOGY.md").write_text("# T\n\nprose\n\n<!-- numbers:start -->\nold\n<!-- numbers:end -->\n\nmore prose\n",
                                        encoding="utf-8")
    doc = ch.main()
    # 2026 has no full-track measurement (one unmeasured, one preview), so it folds into the period before
    assert [p["period"] for p in doc["periods"]] == ["2014-26"]
    p = doc["periods"][0]
    assert p["added"] == 4 and p["measured_full"] == 2 and p["major_share"] == 0.5
    md = (here / "CHRONOLOGY.md").read_text(encoding="utf-8")
    assert "prose" in md and "more prose" in md and "\nold\n" not in md and "| 2014-26 |" in md
    assert (here / "chronology.png").stat().st_size > 1000
