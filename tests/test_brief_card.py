import json

import numpy as np
import soundfile as sf

from synth import SR, three_part_track
from trackcard.brief import LAYER_TYPES, SectionFacts, build_brief, camelot_name
from trackcard.card import to_markdown, write_card
from trackcard.pipeline import analyze

FULL = {"sub": -6.0, "low": 0.0, "mid": -6.0, "high-mid": -12.0, "air": -20.0}


def facts(sid, rel, stems, bands=FULL, flags=(), nxt=None, label=None):
    return SectionFacts(sid=sid, bars="1-16", label=label or sid, rel_level_db=rel, next_jump_db=nxt,
                        stems=stems, band_rel=dict(bands), band_flags=list(flags))


ALL_ON = {"drums": "active", "bass": "active", "vocals": "active", "other": "active"}


def test_full_section_has_no_gaps():
    assert build_brief([facts("S1", 0.0, ALL_ON)], 124, "A minor", "8A", True) == []


def test_missing_drums_and_bass_in_loud_section():
    st = {**ALL_ON, "drums": "absent", "bass": "absent"}
    b = build_brief([facts("S1", -1.0, st)], 124, "A minor", "8A", True)
    layers = [x.layer for x in b]
    assert layers[:2] == ["drums", "bass"]
    assert b[0].bpm_range == "122-126"
    assert b[0].key.startswith("any")  # drums are unpitched
    assert b[1].key.startswith("A minor (8A)")


def test_quiet_section_without_drums_is_not_a_drum_gap():
    st = {**ALL_ON, "drums": "absent", "bass": "absent", "vocals": "absent"}
    b = build_brief([facts("S1", -12.0, st)], 124, "A minor", "8A", True)
    assert "drums" not in [x.layer for x in b]
    assert "pads" in [x.layer for x in b]  # bare, low-energy section gets texture


def test_no_air_flag_in_drop_asks_for_percussion():
    bands = {**FULL, "air": -60.0}
    b = build_brief([facts("S3", 0.0, ALL_ON, bands, ["nothing above 8 kHz"])], 128, "G minor", "6A", True)
    assert b[0].layer == "percussion" and "hats" in b[0].tags
    assert "above 8 kHz" in b[0].evidence


def test_riser_before_a_jump():
    b = build_brief([facts("S1", -9, ALL_ON, nxt=6.0), facts("S2", 0, ALL_ON)], 124, "A minor", "8A", True)
    assert [x.layer for x in b] == ["fx"]


def test_vocals_track_level_and_repeats_merge():
    st = {**ALL_ON, "vocals": "absent", "other": "sparse"}  # nothing carrying the mids
    secs = [facts("S1", 0, st, label="section_1"), facts("S2", 0, ALL_ON, label="section_2"),
            facts("S3", 0, st, label="section_1")]
    secs[2].bars = "33-48"
    b = build_brief(secs, 124, "A minor", "8A", False)
    chords = [x for x in b if x.layer == "chords"]
    assert len(chords) == 1 and chords[0].bars == "1-16, 33-48" and "bars 1-16, 33-48" in chords[0].instruction
    assert b[-1].layer == "vocals" and b[-1].section == "whole track"


def test_all_layers_valid_and_camelot_names():
    assert camelot_name("8A") == "A minor" and camelot_name("8B") == "C major" and camelot_name("6A") == "G minor"
    st = {"drums": "sparse", "bass": "absent", "vocals": "absent", "other": "absent"}
    b = build_brief([facts("S1", 0, st, {**FULL, "sub": -60, "air": -60, "mid": -30},
                           ["nothing above 8 kHz", "mids are scooped (250 Hz-2 kHz)"], nxt=5)], 124, "A minor", "8A", False)
    assert b and all(x.layer in LAYER_TYPES for x in b)


def test_card_without_stems(tmp_path):
    y, _ = three_part_track()
    p = tmp_path / "aba.wav"
    sf.write(p, y, SR)
    card = analyze(p, stems=False)
    md = to_markdown(card)
    order = [md.index(h) for h in ["## 1. Basics", "## 2. Section map", "## 3. Stem activity",
                                    "## 4. Frequency balance", "## 5. Loudness", "## 6. Splice brief"]]
    assert order == sorted(order)
    assert "Stem separation skipped (--no-stems)" in md
    assert "124" in md and "A minor" in md and "8A" in md
    paths = write_card(card, p, tmp_path / "out", json_too=True)
    assert [x.name for x in paths] == ["aba.trackcard.md", "aba.trackcard.json"]
    d = json.loads(paths[1].read_text())
    assert d["camelot"] == "8A" and d["sections"][0]["bars"].startswith("1-")
    assert d["stems_available"] is False


def test_active_vocal_stem_counts_as_mid_content():
    st = {**ALL_ON, "other": "absent", "vocals": "active"}
    assert "chords" not in [x.layer for x in build_brief([facts("S1", 0, st)], 124, "A minor", "8A", True)]
