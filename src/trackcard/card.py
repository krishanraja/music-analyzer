"""Render a TrackCard as Markdown (for pasting into a chat) and JSON."""

from __future__ import annotations

import json
from pathlib import Path

from .brief import camelot_name
from .pipeline import TrackCard
from .spectrum import BAND_NAMES
from .stems import STEMS


def ts(sec: float) -> str:
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def _stem_cell(d: dict) -> str:
    if d["state"] == "absent":
        return "absent"
    return f"{d['state']} {int(round(d['density'] * 100))}%"


METHOD_TEXT = {
    "allin1": "allin1 neural structure model (functional labels)",
    "laplacian": "librosa Laplacian segmentation (FALLBACK: labels are section_N, same N = similar material; they are not intro/verse/drop names)",
    "single": "librosa Laplacian segmentation found only one section (FALLBACK)",
}


def to_markdown(c: TrackCard) -> str:
    L: list[str] = []
    w = L.append
    w(f"# Track card: {c.file}")
    w("")
    w(f"Analysed {c.analyzed_at} by trackcard {c.trackcard_version}, locally from the audio file. "
      f"{c.sample_rate} Hz, {c.channels} ch.")
    w("")
    w("**How to read this card (for an AI with no other context):** trackcard listened to an unreleased "
      "WAV bounce and measured it. Bar numbers count from bar 1 at the first downbeat, 4/4 assumed, so "
      "the producer can point at a moment (\"bar 33\") and you can find it in the section map. "
      "Stem states come from AI source separation (Demucs): *active* = sounding in most of the section, "
      "*sparse* = sounding some of the time, *absent* = essentially silent. Everything here is measured, "
      "not heard; where a method fell back or is uncertain, it says so below. The Splice brief (section 6) "
      "lists sound requests you can act on directly.")
    w("")
    if c.notes:
        w("**Read this first (fallbacks and caveats):**")
        for n in c.notes:
            w(f"- {n}")
        w("")

    # 1. Basics
    w("## 1. Basics")
    w("")
    w(f"- Duration: {ts(c.duration)} ({c.duration:.1f} s)")
    w(f"- BPM: {c.bpm:.1f} measured, likely project tempo **{c.likely_project_bpm}**. "
      f"Confidence {c.bpm_confidence}: {c.bpm_note}.")
    w(f"- Time signature: {c.time_signature}")
    w(f"- Bar grid: {c.downbeat_rule}")
    w(f"- Key: **{c.key}**, Camelot **{c.camelot}**. Confidence {c.key_confidence} "
      f"(runner-up {c.key_runner_up}, margin {c.key_margin:.3f}).")
    compat = ", ".join(f"{camelot_name(k)} ({k})" for k in c.compatible_keys[1:])
    w(f"- Harmonically compatible keys: {compat}")
    w("")

    # 2. Section map
    w("## 2. Section map")
    w("")
    snap = ""
    if c.phrase_snaps:
        snap = (f" {c.phrase_snaps} boundar{'y was' if c.phrase_snaps == 1 else 'ies were'} moved by one bar "
                "onto a 4-bar phrase start (bars 1, 5, 9, ...).")
    w(f"Method: {METHOD_TEXT.get(c.section_method, c.section_method)}.{snap}")
    w("")
    w("| # | label | bars | time | length | energy |")
    w("|---|---|---|---|---|---|")
    for s in c.sections:
        nbars = s.end_bar - s.start_bar + 1
        w(f"| {s.id} | {s.label} | {s.bars} ({nbars} bars) | {ts(s.start)}-{ts(s.end)} | "
          f"{s.end - s.start:.0f} s | {s.energy} ({s.rel_level_db:+.1f} dB) |")
    w("")
    w("Energy is the section's RMS level against the loudest section: peak (within 3 dB), mid (3-8 dB down), low (more than 8 dB down). The loudest sections are the likely drops or peaks.")
    w("")

    # 3. Stems
    w("## 3. Stem activity")
    w("")
    if not c.stems_available:
        w("Not available: stem separation did not run (see 'Read this first').")
    else:
        w(f"Demucs htdemucs on {c.stems_device}, 10 Hz RMS envelopes. Cell = state and % of 100 ms frames where the stem sounds. "
          "'other' holds everything that is not drums, bass or vocals (synths, chords, pads, leads, fx). "
          "On instrumental tracks the vocals stem can pick up vocal-like synths or leads.")
        w("")
        w("| # | bars | " + " | ".join(STEMS) + " |")
        w("|---|---|" + "---|" * len(STEMS))
        for s in c.sections:
            w(f"| {s.id} | {s.bars} | " + " | ".join(_stem_cell(s.stems[n]) for n in STEMS) + " |")
    w("")

    # 4. Frequency balance
    w("## 4. Frequency balance per section")
    w("")
    w("dB relative to the loudest band in that section (0 = loudest). Bands: sub 20-60 Hz, low 60-250 Hz, "
      "mid 250 Hz-2 kHz, high-mid 2-8 kHz, air 8-20 kHz.")
    w("")
    w("| # | bars | " + " | ".join(BAND_NAMES) + " | flags |")
    w("|---|---|" + "---|" * len(BAND_NAMES) + "---|")
    for s in c.sections:
        flags = "; ".join(s.band_flags) if s.band_flags else "balanced"
        w(f"| {s.id} | {s.bars} | " + " | ".join(f"{s.bands_rel_db[b]:.0f}" for b in BAND_NAMES) + f" | {flags} |")
    w("")

    # 5. Loudness
    w("## 5. Loudness and dynamics")
    w("")
    lo = c.loudness
    lufs = f"{lo['integrated_lufs']:.1f} LUFS integrated ({lo['lufs_method']})" if lo["integrated_lufs"] is not None else "LUFS not measured"
    w(f"- Whole track: peak {lo['peak_dbfs']:.1f} dBFS, RMS {lo['rms_dbfs']:.1f} dBFS, {lufs}, crest factor {lo['crest_db']:.1f} dB.")
    if lo["clipped_samples"]:
        w(f"- {lo['clipped_samples']} samples at 0 dBFS (possible clipping).")
    w("- Per section (RMS dBFS, change from the previous section):")
    prev = None
    for s in c.sections:
        delta = "" if prev is None else f" ({s.level_dbfs - prev:+.1f} dB)"
        w(f"  - {s.id} bars {s.bars}: {s.level_dbfs:.1f}{delta}")
        prev = s.level_dbfs
    if c.level_by_8_bars:
        w("- Every 8 bars (RMS dBFS): " + " | ".join(f"{x['bars']}: {x['rms_dbfs']:.0f}" for x in c.level_by_8_bars))
    w("")

    # 6. Splice brief
    w("## 6. Splice brief")
    w("")
    if not c.stems_available:
        w("Stem separation did not run, so only frequency- and level-based gaps are listed (no missing-drums or missing-bass checks).")
        w("")
    w(f"Track facts for every search: **{c.likely_project_bpm} BPM**, **{c.key} ({c.camelot})**. "
      "Each row is a gap trackcard measured, with the evidence. Use the BPM range and key as Splice filters, "
      "the layer as the instrument type, and try the candidate tags in order. Ask the producer before "
      "replacing anything that is already there.")
    w("")
    if not c.brief:
        w("No gaps found by the rules: every section has drums, bass and harmonic content where its energy suggests it should, and the spectrum is full.")
    for i, b in enumerate(c.brief, 1):
        w(f"### {i}. {b.section}, bars {b.bars}: {b.layer}")
        w(f"- Gap: {b.gap}")
        w(f"- Evidence: {b.evidence}")
        w(f"- BPM range: {b.bpm_range}")
        w(f"- Key: {b.key}")
        w(f"- Layer type: {b.layer}")
        w(f"- Candidate Splice tags: {', '.join(b.tags)}")
        w(f"- Do this: {b.instruction}")
        w("")
    return "\n".join(L).rstrip() + "\n"


def to_json(c: TrackCard) -> str:
    return json.dumps(c.to_dict(), indent=2)


def write_card(c: TrackCard, src: Path, out_dir: Path | None = None, json_too: bool = False) -> list[Path]:
    out_dir = out_dir or src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{src.stem}.trackcard.md"
    md.write_text(to_markdown(c), encoding="utf-8")
    paths = [md]
    if json_too:
        js = out_dir / f"{src.stem}.trackcard.json"
        js.write_text(to_json(c), encoding="utf-8")
        paths.append(js)
    return paths
