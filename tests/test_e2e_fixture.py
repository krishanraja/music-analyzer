"""End-to-end on a real bounce. Put a short WAV in tests/fixtures/ (gitignored, never committed).

Runs the full pipeline including Demucs on the first fixture found."""

from pathlib import Path

import pytest

from trackcard.card import to_markdown
from trackcard.pipeline import analyze

FIXTURES = sorted(p for p in (Path(__file__).parent / "fixtures").glob("*") if p.suffix.lower() in {".wav", ".wave"})


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
