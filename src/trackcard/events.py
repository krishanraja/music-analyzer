"""Moments: when each layer enters, leaves, fills out or pulls back, bar by bar,
and where the drops are.

Drops in electronic music are often staged: the bassline drops first and the
full drums join a few bars later. Overall loudness and section clustering miss
this, so trackcard reads the separated stems directly.

Layers tracked: bass, vocals (anything in the vocal range), other (synths,
chords), and the drums stem split into kick (<150 Hz) and tops (>5 kHz,
hats and cymbals), because the kick hides everything else in the drums total.

Method, per layer (from the 10 Hz Demucs RMS envelopes):
- level per bar (dB), using the bar grid;
- at each bar, compare the next 2 bars against the median of the previous 4;
- a rise of 6 dB or more is an event: "enters" if the stem was essentially
  silent before (below -55 dBFS), otherwise "comes in fuller". A fall of 6 dB
  or more is "drops out" or "pulls back". Steps of 6-12 dB need to hold for the
  2 bars; single-bar gaps (a pre-drop cut) do not count as a fall;
- within 2 bars, only the biggest change per stem is kept.

Drop rule: a rise in bass or kick that brings that layer up to its full level
(within 4 dB of its 90th-percentile bar level) after the previous 4 bars sat
10 dB or more below that on average. Any layer rising in the 16 bars after a
drop "joins" it, so a staged drop reads as: bassline/kick first, tops later.
A second bass/kick rise within 4 bars of a drop is part of that drop, not a new one.
Run-up: for each drop, the "cut" is where its lead layer (bass or kick) went
10 dB or more below full and stayed there until the drop, and the "build" is
the earliest layer change in the 16 bars before the drop that comes before
that cut (hats dropping out, a layer pulling back, or the synths/chords
layer swelling in).

Main drop: the last drop that has a run-up and starts before 85% of the
track. That is an arrangement convention (the climax drop comes late, before
the outro), not a loudness measure: staged drops are often mixed to the same
level. With no run-ups at all, the drop the most layers join is the main one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SILENT_DB = -55.0
STEP_DB = 6.0
LOOKBACK_BARS = 4
HOLD_BARS = 2
MERGE_BARS = 2
FULL_WITHIN_DB = 4.0
LOW_BEFORE_DB = 10.0
STAGE_BARS = 16
SAME_DROP_BARS = 4  # a second low-end rise this close to a drop is part of it
DROP_STEMS = ("bass", "kick")
EVENT_LAYERS = ("kick", "bass", "tops", "vocals", "other")
PLURAL = {"kick", "tops", "drums"}  # "kick and low drums", "hats and cymbals"
STEM_WORDS = {"kick": "kick and low drums", "tops": "hats and cymbals (drum tops)", "bass": "bassline",
              "vocals": "vocal-range layer (vocals or a lead)", "other": "synths/chords layer", "drums": "drums"}


@dataclass
class Event:
    time: float
    bar: int
    stem: str
    kind: str  # "enters" | "fuller" | "drops out" | "pulls back"
    delta_db: float
    drop: str = ""  # "" | "drop" | "joins"
    drop_bar: int | None = None  # for "joins": the bar of the drop it belongs to

    def text(self) -> str:
        w = STEM_WORDS.get(self.stem, self.stem)
        pl = self.stem in PLURAL
        if self.kind == "enters":
            core = f"{w} {'enter' if pl else 'enters'}"
        elif self.kind == "fuller":
            core = f"{w} {'come' if pl else 'comes'} in fuller (+{self.delta_db:.0f} dB)"
        elif self.kind == "drops out":
            core = f"{w} {'drop' if pl else 'drops'} out"
        else:
            core = f"{w} {'pull' if pl else 'pulls'} back ({self.delta_db:.0f} dB)"
        if self.drop == "drop":
            return f"DROP: {core}"
        if self.drop == "joins":
            return f"{core}, joining the drop at bar {self.drop_bar}"
        return core


def bar_levels(rms: list[float], frame_hz: int, downbeats: list[float], duration: float) -> np.ndarray:
    r = np.asarray(rms, dtype=float)
    edges = list(downbeats) + [duration]
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        seg = r[int(a * frame_hz) : max(int(b * frame_hz), int(a * frame_hz) + 1)]
        out.append(20 * np.log10(np.sqrt(np.mean(seg**2)) + 1e-9) if len(seg) else -120.0)
    return np.asarray(out)


def stem_events(stem: str, lv: np.ndarray, downbeats: list[float], bar_of) -> list[Event]:
    cands = []
    for i in range(1, len(lv) - 1):
        before = float(np.median(lv[max(0, i - LOOKBACK_BARS) : i]))
        after_bars = lv[i : i + HOLD_BARS]
        after = float(np.mean(after_bars))
        d = after - before
        if d >= STEP_DB and after_bars.min() - before >= STEP_DB / 2:
            kind = "enters" if before < SILENT_DB else "fuller"
        elif d <= -STEP_DB and after_bars.max() - before <= -STEP_DB / 2:
            kind = "drops out" if after < SILENT_DB else "pulls back"
        else:
            continue
        cands.append(Event(float(downbeats[i]), bar_of(downbeats[i]), stem, kind, round(d, 1)))
    # keep the biggest change within MERGE_BARS, per direction
    kept: list[Event] = []
    for e in sorted(cands, key=lambda e: -abs(e.delta_db)):
        if all(abs(e.bar - k.bar) > MERGE_BARS or (e.delta_db > 0) != (k.delta_db > 0) for k in kept):
            kept.append(e)
    return sorted(kept, key=lambda e: e.time)


RUNUP_BARS = 16
MAIN_BEFORE = 0.85


@dataclass
class Drop:
    time: float
    bar: int
    lead: str  # the layer that drops first
    joins: list[Event]
    main: bool = False
    cut_time: float | None = None
    cut_bar: int | None = None
    cut_bars: int = 0  # how many bars the lead layer was cut before the drop
    build_time: float | None = None
    build_bar: int | None = None
    build_what: str = ""

    @property
    def has_runup(self) -> bool:
        return self.cut_bar is not None or self.build_bar is not None

    def text(self) -> str:
        first = STEM_WORDS.get(self.lead, self.lead)
        if not self.joins:
            return f"{first} {'drop' if self.lead in PLURAL else 'drops'} in"
        later = "; ".join(f"{STEM_WORDS.get(j.stem, j.stem)} at bar {j.bar}" for j in self.joins)
        return f"staged: {first} first, then {later}"


def _runup(d: Drop, i: int, lv: np.ndarray, events: list[Event], downbeats: list[float], bar_of) -> None:
    full = float(np.percentile(lv, 90))
    j = i
    while j - 1 >= max(0, i - RUNUP_BARS) and lv[j - 1] <= full - LOW_BEFORE_DB:
        j -= 1
    if j < i:
        d.cut_time, d.cut_bar, d.cut_bars = float(downbeats[j]), bar_of(downbeats[j]), i - j
    limit = d.cut_bar if d.cut_bar is not None else d.bar
    window = [e for e in events
              if d.bar - RUNUP_BARS <= e.bar < limit
              and (e.delta_db < 0 or (e.stem == "other" and e.delta_db > 0))]
    if window:
        first = min(window, key=lambda e: e.time)
        same = [e for e in window if e.bar == first.bar]
        d.build_time, d.build_bar = first.time, first.bar
        d.build_what = "; ".join(e.text() for e in same)


def mark_drops(events: list[Event], levels: dict[str, np.ndarray], bar_index,
               downbeats: list[float] | None = None, duration: float | None = None, bar_of=None) -> list[Drop]:
    drops: list[Drop] = []
    for e in sorted(events, key=lambda e: e.time):
        if e.stem not in DROP_STEMS or e.delta_db <= 0:
            continue
        lv = levels[e.stem]
        full = float(np.percentile(lv, 90))
        i = bar_index(e.time)
        after = float(np.mean(lv[i : i + HOLD_BARS]))
        before = lv[max(0, i - LOOKBACK_BARS) : i]
        if not (after >= full - FULL_WITHIN_DB and len(before) >= LOOKBACK_BARS
                and float(np.mean(before)) <= full - LOW_BEFORE_DB):
            continue
        if drops and e.bar - drops[-1].bar <= SAME_DROP_BARS:  # bass and kick a bar apart: one drop
            e.drop, e.drop_bar = "joins", e.bar
            drops[-1].joins.append(e)
            continue
        e.drop = "drop"
        d = Drop(e.time, e.bar, e.stem, [])
        if downbeats is not None and bar_of is not None:
            _runup(d, i, lv, events, downbeats, bar_of)
        drops.append(d)
    for e in sorted(events, key=lambda e: e.time):
        if e.drop or e.delta_db <= 0:
            continue
        owner = [d for d in drops if 0 < e.bar - d.bar <= STAGE_BARS]
        if owner:
            d = owner[-1]
            e.drop, e.drop_bar = "joins", d.bar
            d.joins.append(e)
    if drops:
        end = duration if duration else max(d.time for d in drops) + 1.0
        late = [d for d in drops if d.has_runup and d.time < MAIN_BEFORE * end]
        if late:
            late[-1].main = True
        else:
            lead_rise = {e.bar: e.delta_db for e in events if e.drop == "drop"}
            max(drops, key=lambda d: (len(d.joins), lead_rise.get(d.bar, 0.0))).main = True
    return drops


def detect_events(envelopes: dict, frame_hz: int, downbeats: list[float], duration: float, bar_of) -> tuple[list[Event], list[Drop]]:
    if len(downbeats) < LOOKBACK_BARS + HOLD_BARS + 1:
        return [], []
    db = np.asarray(downbeats)

    def bar_index(t: float) -> int:
        return int(np.argmin(np.abs(db - t)))

    levels = {s: bar_levels(envelopes[s]["rms"], frame_hz, downbeats, duration)
              for s in EVENT_LAYERS if s in envelopes}
    events: list[Event] = []
    for s, lv in levels.items():
        events += stem_events(s, lv, downbeats, bar_of)
    drops = mark_drops(events, levels, bar_index, downbeats, duration, bar_of)
    return sorted(events, key=lambda e: (e.time, e.drop != "drop")), drops
