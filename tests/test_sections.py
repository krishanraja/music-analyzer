import numpy as np
import soundfile as sf

from synth import SR, click_track, three_part_track
from trackcard.audio import load_audio
from trackcard.sections import Section, choose_k, detect_sections, merge_short, renumber, snap_to_downbeats
from trackcard.tempo import analyze_tempo


def test_merge_short_absorbs_into_longer_neighbour():
    secs = [Section(0, 20, "a"), Section(20, 23, "b"), Section(23, 30, "c"), Section(30, 60, "a")]
    out = merge_short(secs, 8.0)
    assert all(s.length >= 8.0 for s in out)
    assert out[0].start == 0 and out[-1].end == 60
    assert sum(s.length for s in out) == 60


def test_renumber_in_order_of_appearance():
    out = renumber([Section(0, 10, "section_3"), Section(10, 20, "section_1"), Section(20, 30, "section_3")])
    assert [s.label for s in out] == ["section_1", "section_2", "section_1"]


def test_choose_k_eigengap():
    assert choose_k(np.array([0, 0.01, 0.02, 0.03, 0.5, 0.6, 0.7])) == 4


def test_snap_to_downbeats():
    secs = [Section(0, 31.2, "a"), Section(31.2, 60, "b")]
    out = snap_to_downbeats(secs, [0, 8, 16, 24, 32, 40], bar_s=8)
    assert out[0].end == 32 and out[1].start == 32


def test_laplacian_on_synthetic_aba(tmp_path):
    y, (b1, b2) = three_part_track()
    p = tmp_path / "aba.wav"
    sf.write(p, y, SR)
    a = load_audio(p)
    t = analyze_tempo(a.y, a.sr)
    m = detect_sections(p, a.y, a.sr, t.beats, mode="laplacian")
    assert m.method == "laplacian"
    bar = 4 * 60 / 124.0
    starts = [s.start for s in m.sections[1:]]
    # both true boundaries are found within about a bar
    for truth in (b1, b2):
        assert min(abs(s - truth) for s in starts) <= bar * 1.1, (starts, truth)
    # the drums+bass B section gets a label of its own, and the pad-only A' repeats an A label
    def label_at(tm):
        return next(s.label for s in m.sections if s.start <= tm < s.end)

    assert label_at((b1 + b2) / 2) not in {label_at(b1 - 3), label_at(b2 + 10)}
    assert label_at(b2 + 10) in {s.label for s in m.sections if s.end <= b1 + 1}


def test_auto_mode_falls_back_and_says_why(tmp_path):
    y, _ = three_part_track()
    p = tmp_path / "aba.wav"
    sf.write(p, y, SR)
    a = load_audio(p)
    t = analyze_tempo(a.y, a.sr)
    m = detect_sections(p, a.y, a.sr, t.beats, mode="auto")
    # allin1 cannot run in the test environment, so the fallback must be named
    if m.method != "allin1":
        assert any("fell back to librosa Laplacian" in n for n in m.notes)


def test_bar_one_rule_uses_bounce_start(tmp_path):
    # Accent on the third beat (kick/bass would vote for it), but the bounce starts on a beat.
    bpm = 120.0
    y = click_track(bpm, 30, accent_downbeat=False)
    beat = int(SR * 60 / bpm)
    from synth import kick, place

    for i in range(2, 60, 4):
        place(y, kick() * 0.8, i * 60 / bpm)
    p = tmp_path / "c.wav"
    sf.write(p, y, SR)
    a = load_audio(p)
    t = analyze_tempo(a.y, a.sr)
    assert t.downbeats[0] < 0.05
    assert "bar 1 = first beat" in t.downbeat_rule
    assert t.bar_at(0.0) == 1 and t.bar_at(2.05) == 2


def test_snap_to_phrases_moves_one_bar_onto_phrase_start():
    from trackcard.sections import snap_to_phrases

    db = [float(i * 2) for i in range(40)]  # bar n starts at 2*(n-1) s
    secs = [Section(0, 46, "a"), Section(46, 80, "b")]  # boundary at bar 24 -> phrase start is bar 25 (48 s)
    out, moved = snap_to_phrases(secs, db)
    assert moved == 1 and out[0].end == 48 and out[1].start == 48
    secs2 = [Section(0, 40, "a"), Section(40, 80, "b")]  # bar 21 is already a phrase start
    out2, moved2 = snap_to_phrases(secs2, db)
    assert moved2 == 0 and out2[1].start == 40
    secs3 = [Section(0, 44, "a"), Section(44, 80, "b")]  # bar 23: two bars from 21 and 25 -> left alone
    assert snap_to_phrases(secs3, db)[1] == 0
