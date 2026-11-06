"""Moment and drop detection on hand-made 10 Hz envelopes (no Demucs needed)."""

import numpy as np

from trackcard.events import detect_events

FHZ = 10
BAR = 2.0  # seconds per bar (120 BPM)


def env_from_bars(levels_db):
    """One RMS value per 100 ms frame, constant within each 2 s bar."""
    rms = np.repeat(10 ** (np.asarray(levels_db, dtype=float) / 20), int(BAR * FHZ))
    return {"rms": rms.tolist(), "peak": rms.tolist()}


def run(layers, n_bars):
    downbeats = [i * BAR for i in range(n_bars)]
    bar_of = lambda t: int(round(t / BAR)) + 1  # noqa: E731
    return detect_events({k: env_from_bars(v) for k, v in layers.items()}, FHZ, downbeats, n_bars * BAR, bar_of)


def test_staged_drop_bass_first_then_tops():
    n = 40
    kick = [-80] * 16 + [-20] * 24          # low end drops at bar 17
    tops = [-80] * 20 + [-30] * 20          # hats join at bar 21
    bass = [-80] * 16 + [-22] * 24
    other = [-30] * 40                      # pad throughout
    events, drops = run({"kick": kick, "tops": tops, "bass": bass, "other": other}, n)
    assert len(drops) == 1 and drops[0].main
    d = drops[0]
    assert d.bar == 17 and d.lead in {"kick", "bass"}
    assert {j.stem for j in d.joins} >= {"tops"} and any(j.bar == 21 for j in d.joins)
    assert d.text().startswith("staged:") and "bar 21" in d.text()


def test_one_bar_gap_before_drop_is_not_an_exit():
    kick = [-40] * 12 + [-80] + [-20] * 12  # light kick, one-bar cut, full kick
    events, drops = run({"kick": kick, "other": [-30] * 25}, 25)
    assert not any(e.stem == "kick" and e.delta_db < 0 for e in events)
    assert drops and drops[0].bar == 14


def test_steady_track_has_no_moments():
    events, drops = run({"kick": [-20] * 30, "bass": [-22] * 30, "tops": [-30] * 30, "other": [-28] * 30}, 30)
    assert events == [] and drops == []


def test_main_drop_is_the_one_most_layers_join():
    n = 80
    kick = [-80] * 8 + [-20] * 16 + [-80] * 16 + [-20] * 40   # drops at bar 9 and bar 41
    tops = [-80] * 44 + [-30] * 36                              # joins only the second drop
    voc = [-80] * 48 + [-30] * 32
    events, drops = run({"kick": kick, "tops": tops, "vocals": voc, "other": [-30] * n}, n)
    assert [d.bar for d in drops] == [9, 41]
    assert [d.main for d in drops] == [False, True]


def test_bass_then_kick_a_bar_apart_is_one_drop():
    bass = [-80] * 8 + [-22] * 24
    kick = [-80] * 9 + [-20] * 23
    events, drops = run({"bass": bass, "kick": kick, "other": [-30] * 32}, 32)
    assert len(drops) == 1 and drops[0].bar == 9 and drops[0].lead == "bass"
    assert [j.stem for j in drops[0].joins] == ["kick"]


def test_runup_build_and_cut():
    n = 32
    kick = [-20] * 12 + [-80] * 4 + [-20] * 16       # cut bars 13-16, drop bar 17
    tops = [-30] * 8 + [-80] * 8 + [-30] * 16        # hats out from bar 9
    events, drops = run({"kick": kick, "tops": tops, "other": [-30] * n}, n)
    d = next(x for x in drops if x.bar == 17)
    assert d.cut_bar == 13 and d.cut_bars == 4
    assert d.build_bar == 9 and "hats" in d.build_what


def test_main_drop_is_the_later_one_with_a_runup_even_with_fewer_joins():
    n = 100
    kick = ([-20] * 12 + [-80] * 4) + [-20] * 32 + ([-80] * 4) + [-20] * 48   # drops at 17 and 53
    voc = [-80] * 16 + [-30] * 8 + [-80] * 76            # joins only the first drop
    tops = [-80] * 18 + [-30] * 82
    events, drops = run({"kick": kick, "vocals": voc, "tops": tops, "other": [-30] * n}, n)
    assert [d.bar for d in drops] == [17, 53]
    assert len(drops[0].joins) > len(drops[1].joins)
    assert [d.main for d in drops] == [False, True]
