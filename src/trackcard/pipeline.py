"""Runs every analysis stage and assembles a TrackCard."""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import __version__
from .audio import load_audio
from .brief import SectionFacts, Suggestion, build_brief
from .key import PITCHES, analyze_key, camelot_of, compatible_camelot, parse_key
from .audio import TrackcardError
from .loudness import analyze_loudness, span_rms_dbfs
from .sections import detect_sections, snap_to_downbeats, snap_to_phrases
from .spectrum import band_profile
from .stems import STEMS, StemResult, separate, stem_activity
from .tempo import BEATS_PER_BAR, analyze_tempo

Progress = Callable[[str], None]


@dataclass
class SectionInfo:
    id: str
    label: str
    start: float
    end: float
    start_bar: int
    end_bar: int
    level_dbfs: float
    rel_level_db: float
    energy: str
    stems: dict[str, dict] | None
    bands_rel_db: dict[str, float]
    band_flags: list[str]

    @property
    def bars(self) -> str:
        return f"{self.start_bar}-{self.end_bar}"


@dataclass
class TrackCard:
    file: str
    analyzed_at: str
    trackcard_version: str
    duration: float
    sample_rate: int
    channels: int
    bpm: float
    bpm_source: str  # "measured" | "producer"
    measured_bpm: float | None
    likely_project_bpm: int
    raw_bpm: float
    bpm_confidence: str
    bpm_note: str
    octave_corrected: bool
    downbeat_rule: str
    time_signature: str
    key: str
    key_source: str  # "measured" | "producer"
    measured_key: str
    key_alternative: str | None
    key_alternative_camelot: str | None
    key_alternative_reason: str
    camelot: str
    compatible_keys: list[str]
    key_confidence: str
    key_runner_up: str
    key_margin: float
    section_method: str
    phrase_snaps: int
    sections: list[SectionInfo]
    stems_available: bool
    stems_device: str
    loudness: dict
    level_by_8_bars: list[dict]
    brief: list[Suggestion]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for s, src in zip(d["sections"], self.sections):
            s["bars"] = src.bars
        return d


def _energy(rel: float) -> str:
    if rel >= -3.0:
        return "peak"
    if rel >= -8.0:
        return "mid"
    return "low"


def analyze(
    path: str | Path,
    sections_mode: str = "auto",
    stems: bool = True,
    progress: Progress | None = None,
    bpm: float | None = None,
    key: str | None = None,
) -> TrackCard:
    """bpm / key: values the producer already knows. They override the measurement
    (which is still run and shown next to them)."""
    say = progress or (lambda _m: None)
    notes: list[str] = []
    if bpm is not None and not 40 <= float(bpm) <= 250:
        raise TrackcardError(f"BPM {bpm} is out of range (40-250).")
    try:
        key_override = parse_key(key) if key else None
    except ValueError as exc:
        raise TrackcardError(str(exc)) from exc

    say("Loading audio")
    a = load_audio(path)

    say("Tracking tempo and beats")
    measured_bpm = None
    if bpm is not None:
        measured_bpm = analyze_tempo(a.y, a.sr).bpm
    tempo = analyze_tempo(a.y, a.sr, bpm_override=float(bpm) if bpm is not None else None)
    notes += tempo.notes

    say("Detecting key")
    measured = analyze_key(a.y, a.sr)
    key = measured
    if key_override is not None:
        kp, kmaj = key_override
        from dataclasses import replace

        key = replace(measured, tonic=PITCHES[kp], mode="major" if kmaj else "minor",
                      camelot=camelot_of(kp, kmaj), alternative=None, alternative_camelot=None, alternative_reason="")
    if key.mode_tiebreak_used and key_override is None:
        notes.append(
            f"Key: major/minor was a near tie, so the louder tonic decided ({key.name}; runner-up {key.runner_up})."
        )

    say("Finding sections")
    smap = detect_sections(a.path, a.y, a.sr, tempo.beats, mode=sections_mode)
    notes += smap.notes
    if smap.method == "allin1" and smap.downbeats and bpm is None:
        tempo.beats, tempo.downbeats = smap.beats, smap.downbeats
        tempo.bpm = round(smap.bpm, 1)
        tempo.downbeat_rule = "beats and downbeats from allin1"
    bar_s = BEATS_PER_BAR * 60.0 / tempo.bpm
    secs = snap_to_downbeats(smap.sections, tempo.downbeats, bar_s)
    phrase_moves = 0
    if smap.method != "allin1":
        secs, phrase_moves = snap_to_phrases(secs, tempo.downbeats)

    stem_res: StemResult
    if stems:
        say("Separating stems with Demucs htdemucs (slow on CPU: roughly 1-2x track length)")
        stem_res = separate(a.native, a.native_sr)
    else:
        stem_res = StemResult(available=False, notes=["Stem separation skipped (--no-stems)."])
    notes += stem_res.notes

    say("Measuring frequency balance and loudness")
    loud = analyze_loudness(a.native, a.native_sr)
    notes += loud.notes
    mono = a.native.mean(axis=1)
    spans = [(s.start, s.end) for s in secs]
    levels = [span_rms_dbfs(a.native, a.native_sr, s, e) for s, e in spans]
    top = max(levels)
    acts = (
        {n: stem_activity(stem_res.envelopes[n], stem_res.frame_hz, spans) for n in STEMS}
        if stem_res.available
        else None
    )

    infos: list[SectionInfo] = []
    for i, (s, lvl) in enumerate(zip(secs, levels)):
        sid = f"S{i + 1}"
        sb = tempo.bar_at(s.start + 1e-3)
        eb = max(tempo.bar_at(s.end - 1e-3), sb)
        prof = band_profile(mono, a.native_sr, s.start, s.end)
        st = (
            {n: {"state": acts[n][i].state, "density": acts[n][i].density, "level_dbfs": acts[n][i].level_db}
             for n in STEMS}
            if acts
            else None
        )
        infos.append(
            SectionInfo(
                id=sid, label=s.label, start=round(s.start, 2), end=round(s.end, 2),
                start_bar=sb, end_bar=eb, level_dbfs=lvl, rel_level_db=round(lvl - top, 1),
                energy=_energy(lvl - top), stems=st, bands_rel_db=prof.rel_db, band_flags=prof.flags,
            )
        )

    vocals_anywhere = None
    if acts:
        vocals_anywhere = any(a_.state != "absent" for a_ in acts["vocals"])
    facts = [
        SectionFacts(
            sid=si.id, bars=si.bars, label=si.label, rel_level_db=si.rel_level_db,
            next_jump_db=(infos[j + 1].level_dbfs - si.level_dbfs) if j + 1 < len(infos) else None,
            stems={k: v["state"] for k, v in si.stems.items()} if si.stems else None,
            band_rel=si.bands_rel_db, band_flags=si.band_flags,
        )
        for j, si in enumerate(infos)
    ]
    alt = (key.alternative, key.alternative_camelot) if key.alternative else None
    brief = build_brief(facts, tempo.bpm, key.name, key.camelot, vocals_anywhere, alt)

    # level per 8 bars
    strip = []
    db = tempo.downbeats
    if db:
        for i in range(0, len(db), 8):
            s = db[i]
            e = db[i + 8] if i + 8 < len(db) else a.duration
            if e - s < 1.0:
                continue
            strip.append({
                "bars": f"{tempo.bar_at(s + 1e-3)}-{tempo.bar_at(e - 1e-3)}",
                "start": round(s, 2),
                "rms_dbfs": span_rms_dbfs(a.native, a.native_sr, s, e),
            })

    return TrackCard(
        file=a.path.name,
        analyzed_at=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        trackcard_version=__version__,
        duration=round(a.duration, 2),
        sample_rate=a.native_sr,
        channels=a.channels,
        bpm=tempo.bpm,
        bpm_source="producer" if bpm is not None else "measured",
        measured_bpm=measured_bpm,
        likely_project_bpm=int(round(tempo.bpm)),
        raw_bpm=tempo.raw_bpm,
        bpm_confidence=tempo.confidence,
        bpm_note=tempo.confidence_note,
        octave_corrected=tempo.octave_corrected,
        downbeat_rule=tempo.downbeat_rule,
        time_signature="4/4 (assumed)",
        key=key.name,
        key_source="producer" if key_override is not None else "measured",
        measured_key=f"{measured.name} ({measured.camelot})",
        key_alternative=key.alternative,
        key_alternative_camelot=key.alternative_camelot,
        key_alternative_reason=key.alternative_reason,
        camelot=key.camelot,
        compatible_keys=compatible_camelot(key.camelot),
        key_confidence=key.confidence,
        key_runner_up=key.runner_up,
        key_margin=key.margin,
        section_method=smap.method,
        phrase_snaps=phrase_moves,
        sections=infos,
        stems_available=stem_res.available,
        stems_device=f"{stem_res.device} (shifts={stem_res.shifts})" if stem_res.available else "",
        loudness={
            "peak_dbfs": loud.peak_dbfs,
            "rms_dbfs": loud.rms_dbfs,
            "crest_db": loud.crest_db,
            "integrated_lufs": loud.integrated_lufs,
            "lufs_method": loud.lufs_method,
            "clipped_samples": loud.clipped_samples,
        },
        level_by_8_bars=strip,
        brief=brief,
        notes=notes,
    )
