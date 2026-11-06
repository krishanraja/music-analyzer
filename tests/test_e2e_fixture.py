"""End-to-end tests on real bounces. Nothing here is committed: tests/fixtures/ is gitignored.

1. test_real_bounce_end_to_end runs the full pipeline (with Demucs) on the first WAV found.
2. test_matches_producer_truth checks trackcard against what the producer heard, from an
   optional tests/fixtures/truth.json, for example:

   {
     "closure.wav": {"bpm": 125, "key": "D minor"},
     "closure_latest.wav": {"bpm": 125, "key": "D minor", "events": {"3:04": "main drop", "3:20": "tops"}}
   }

   bpm must match within 0.1; key must be the main key or the stated alternative; each
   drop time must start a section (within one bar) whose energy is "peak".
   "events" maps a time to what happens there: "main drop", "drop", "cut" (the low
   end goes out before a drop), "build" (a drop's run-up starts), or a layer name
   (kick, tops, bass, vocals, other) that must rise within one bar of that time.
   Everything matches within one bar, except "build", which matches within four:
   where a build "starts" is fuzzy by ear and by measurement.
"""

import json
from pathlib import Path

import pytest

from trackcard.card import to_markdown
from trackcard.key import PITCHES, parse_key
from trackcard.pipeline import analyze

FIX = Path(__file__).parent / "fixtures"
FIXTURES = sorted(p for p in FIX.glob("*") if p.suffix.lower() in {".wav", ".wave"})
TRUTH_FILE = FIX / "truth.json"
TRUTH = json.loads(TRUTH_FILE.read_text()) if TRUTH_FILE.exists() else {}


@pytest.mark.slow
@pytest.mark.skipif(not FIXTURES, reason="no WAV in tests/fixtures/ (add your own bounce to run this)")
def test_real_bounce_end_to_end():
    card = analyze(FIXTURES[0])
    md = to_markdown(card)
    assert 60 <= card.bpm <= 200
    assert card.camelot[-1] in "AB"
    assert card.sections and card.sections[0].start == 0.0
    assert abs(card.sections[-1].end - card.duration) < 0.5
    assert all(s.start_bar <= s.end_bar for s in card.sections)
    for h in ["## 1. Basics", "## 2. Section map", "## 3. Stem activity", "## 4. Frequency balance",
              "## 5. Loudness", "## 6. Splice brief"]:
        assert h in md
    if card.section_method != "allin1":
        assert any("fell back" in n or "single section" in n for n in card.notes)


def _secs(mmss: str) -> float:
    m, s = mmss.split(":")
    return int(m) * 60 + float(s)


def _key_name(text: str) -> str:
    p, maj = parse_key(text)
    return f"{PITCHES[p]} {'major' if maj else 'minor'}"


@pytest.mark.slow
@pytest.mark.skipif(not TRUTH, reason="no tests/fixtures/truth.json (add what you heard to run this)")
@pytest.mark.parametrize("name", sorted(TRUTH))
def test_matches_producer_truth(name):
    path = FIX / name
    if not path.exists():
        pytest.skip(f"{name} is listed in truth.json but not in tests/fixtures/")
    truth = TRUTH[name]
    card = analyze(path, stems="events" in truth)  # moments need stems; the rest does not
    misses = []
    if "bpm" in truth and abs(card.bpm - truth["bpm"]) > 0.1:
        misses.append(f"BPM {card.bpm} vs {truth['bpm']}")
    if "key" in truth:
        want = _key_name(truth["key"])
        if want not in {card.key, card.key_alternative}:
            misses.append(f"key {card.key} / alt {card.key_alternative} vs {want}")
    bar = 240.0 / card.bpm
    for d in truth.get("drops", []):
        t = _secs(d)
        hits = [s for s in card.sections if abs(s.start - t) <= bar]
        if not hits or hits[0].energy != "peak":
            misses.append(f"no peak section starting within one bar of {d}")
    for when, what in truth.get("events", {}).items():
        t = _secs(when)
        if what in ("main drop", "drop"):
            ok = any(abs(d["time"] - t) <= bar and (d["main"] or what == "drop") for d in card.drops)
        elif what == "cut":
            ok = any(d.get("cut") and abs(d["cut"]["time"] - t) <= bar for d in card.drops)
        elif what == "build":
            ok = any(d.get("build") and abs(d["build"]["time"] - t) <= 4 * bar for d in card.drops)
        else:
            ok = any(m["layer"] == what and m["delta_db"] > 0 and abs(m["time"] - t) <= bar for m in card.moments)
        if not ok:
            misses.append(f"no {what} within one bar of {when}")
    assert not misses, "; ".join(misses)
