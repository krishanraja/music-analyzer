"""Tempo, beat grid and downbeats.

Methods (reimplemented from the approach published in MixMind):
- librosa beat tracking, then tempo octave correction: when the first BPM is
  outside 85-170, beat tracking is re-run with hints at x0.5, x2/3, x1.5 and x2,
  each candidate scored by mean onset strength at its beats (out-of-range
  candidates penalised), and the best one kept.
- Downbeat phase: which of the 4 beat offsets is beat 1, chosen by onset
  strength in a low band (<250 Hz, kick and bass) only.

Added in trackcard:
- Bar 1 rule: a DAW bounce exported from bar 1 starts with sound on beat 1.
  When the file starts with sound within 50 ms and a tracked beat sits within
  a quarter beat of that start, that beat is bar 1. Otherwise the low-band
  phase pick above decides. Kick/bass energy alone cannot tell beat 1 from
  beat 3 in four-on-the-floor music, so the bounce start is the better
  anchor when it exists. The card says which rule was used.
- Off-beat check: if the low-band onset envelope is clearly stronger half a
  beat later than at the tracked beats, the grid is shifted by half a beat.
  This catches the tracker locking onto loud off-beat hats.
- BPM is the slope of a straight-line fit through the beat times, which is
  more precise than the median of frame-quantised beat intervals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PREFERRED_MIN = 85.0
PREFERRED_MAX = 170.0
HINT_MULTIPLIERS = (0.5, 2.0 / 3.0, 1.5, 2.0)
OUT_OF_RANGE_WEIGHT = 0.6
LOW_BAND_HZ = 250.0
BEATS_PER_BAR = 4


@dataclass
class TempoResult:
    bpm: float
    beats: list[float]
    downbeats: list[float]
    raw_bpm: float
    octave_corrected: bool
    confidence: str  # "high" | "medium" | "low"
    confidence_note: str
    downbeat_rule: str = ""
    notes: list[str] = field(default_factory=list)

    def bar_at(self, t: float) -> int:
        """1-indexed bar number containing time t. Bar 1 starts at the first downbeat
        (times within a quarter beat before it count as bar 1). Earlier times are a
        pickup and return 0."""
        db = self.downbeats
        if not db:
            return 1
        beat = 60.0 / self.bpm
        if t < db[0] - beat / 4:
            return 0
        idx = max(int(np.searchsorted(db, t + 1e-6, side="right")), 1)
        if idx >= len(db):
            bar_len = BEATS_PER_BAR * beat
            return len(db) + int((t - db[-1]) // bar_len)
        return idx

    def snap_to_downbeat(self, t: float, max_dist: float | None = None) -> float:
        if not self.downbeats:
            return t
        db = np.asarray(self.downbeats)
        i = int(np.argmin(np.abs(db - t)))
        if max_dist is not None and abs(db[i] - t) > max_dist:
            return t
        return float(db[i])


def _strength(frames: np.ndarray, env: np.ndarray, slop: int = 0) -> float:
    """Mean onset strength at the given frames. With slop > 0, take the peak
    within +/-slop frames, since tracked beats can sit a frame or two off the
    onset peak."""
    valid = frames[(frames >= 0) & (frames < len(env))]
    if not len(valid):
        return 0.0
    if slop == 0:
        return float(env[valid].mean())
    return float(np.mean([env[max(f - slop, 0) : f + slop + 1].max() for f in valid]))


def _bpm_from_frames(frames: np.ndarray, sr: int) -> float | None:
    import librosa

    if len(frames) < 2:
        return None
    t = librosa.frames_to_time(frames, sr=sr)
    d = np.diff(t)
    if d.min() <= 0:
        return None
    return 60.0 / float(np.median(d))


def correct_octave(y, sr, bpm0, frames0, onset_env):
    """Return (bpm, frames, corrected?)."""
    import librosa

    if PREFERRED_MIN <= bpm0 <= PREFERRED_MAX:
        return bpm0, frames0, False

    def score(bpm, frames):
        w = 1.0 if PREFERRED_MIN <= bpm <= PREFERRED_MAX else OUT_OF_RANGE_WEIGHT
        return _strength(frames, onset_env) * w

    best = (score(bpm0, frames0), bpm0, frames0)
    for m in HINT_MULTIPLIERS:
        hint = bpm0 * m
        if not 50.0 <= hint <= 220.0:
            continue
        try:
            _, fr = librosa.beat.beat_track(
                y=y, sr=sr, onset_envelope=onset_env, start_bpm=hint, trim=False
            )
        except Exception:
            continue
        b = _bpm_from_frames(fr, sr)
        if b is None:
            continue
        s = score(b, fr)
        if s > best[0]:
            best = (s, b, fr)
    _, bpm, frames = best
    return bpm, frames, bpm != bpm0


def low_band_onset_env(y, sr):
    import librosa

    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
    freqs = librosa.mel_frequencies(n_mels=128, fmin=0.0, fmax=sr / 2.0)
    low = freqs <= LOW_BAND_HZ
    if not low.any():
        low[0] = True
    return librosa.onset.onset_strength(S=librosa.power_to_db(mel[low], ref=np.max), sr=sr)


def downbeat_phase(frames: np.ndarray, env: np.ndarray) -> int:
    best, best_s = 0, -np.inf
    for off in range(BEATS_PER_BAR):
        s = _strength(frames[off::BEATS_PER_BAR], env, slop=2)
        if s > best_s:
            best, best_s = off, s
    return best


def fit_bpm(beats: list[float]) -> float | None:
    if len(beats) < 4:
        return None
    idx = np.arange(len(beats))
    slope = float(np.polyfit(idx, np.asarray(beats), 1)[0])
    return 60.0 / slope if slope > 0 else None


OFFBEAT_RATIO = 1.5


def offbeat_shift(beats: list[float], env: np.ndarray, sr: int, hop: int = 512) -> tuple[list[float], bool]:
    """Shift the grid by half a beat if the kick/bass band says the tracker is on the off-beat."""
    if len(beats) < 8:
        return beats, False
    b = np.asarray(beats)
    half = float(np.median(np.diff(b))) / 2.0

    def strength(times):
        fr = np.round(times * sr / hop).astype(int)
        fr = fr[(fr >= 0) & (fr < len(env))]
        # allow +/-1 frame of slop
        vals = [env[max(f - 1, 0) : f + 2].max() for f in fr]
        return float(np.mean(vals)) if vals else 0.0

    on, off = strength(b), strength(b + half)
    if off > OFFBEAT_RATIO * on:
        shifted = b + half
        if b[0] - half >= 0:
            shifted = np.concatenate([[b[0] - half], shifted])
        dur = len(env) * hop / sr
        return [float(t) for t in shifted if t < dur], True
    return beats, False


def first_sound(y: np.ndarray, sr: int, thresh_db: float = -40.0) -> float | None:
    idx = np.flatnonzero(np.abs(y) > 10 ** (thresh_db / 20))
    return float(idx[0] / sr) if len(idx) else None


def extend_back(beats: list[float]) -> list[float]:
    """Add beats before the first tracked one if the grid fits there (the tracker
    often skips the first beat or two)."""
    if len(beats) < 4:
        return beats
    period = float(np.median(np.diff(beats[:16])))
    out = list(beats)
    while out[0] - period >= -0.03:
        out.insert(0, max(out[0] - period, 0.0))
    return out


def _confidence(beats: list[float], corrected: bool) -> tuple[str, str]:
    if len(beats) < 8:
        return "low", "very few beats detected; treat BPM as a guess"
    iv = np.diff(beats)
    cv = float(np.std(iv) / max(np.mean(iv), 1e-9))
    parts = []
    if cv < 0.02:
        level = "high"
        parts.append(f"beat spacing is steady (variation {cv*100:.1f}%)")
    elif cv < 0.06:
        level = "medium"
        parts.append(f"beat spacing varies by {cv*100:.1f}%")
    else:
        level = "low"
        parts.append(f"beat spacing is unsteady ({cv*100:.1f}% variation); grid may drift")
    if corrected:
        parts.append("tempo was octave-corrected, so half or double time is possible")
        if level == "high":
            level = "medium"
    return level, "; ".join(parts)


def analyze_tempo(y: np.ndarray, sr: int) -> TempoResult:
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    bpm_arr, frames = librosa.beat.beat_track(y=y, sr=sr, onset_envelope=onset_env, trim=False)
    raw = float(np.atleast_1d(bpm_arr)[0])
    bpm, frames, corrected = correct_octave(y, sr, raw, frames, onset_env)
    beats = librosa.frames_to_time(frames, sr=sr).tolist()
    fitted = fit_bpm(beats)
    if fitted is not None:
        bpm = fitted
    notes = []
    low_env = low_band_onset_env(y, sr)
    beats, shifted = offbeat_shift(beats, low_env, sr)
    if shifted:
        notes.append(
            "Beat grid moved by half a beat: the tracker had locked onto the off-beat "
            "(the kick/bass band hits half a beat later)."
        )
    if corrected:
        notes.append(
            f"Tempo octave correction: first pass said {raw:.1f} BPM (outside {PREFERRED_MIN:.0f}-"
            f"{PREFERRED_MAX:.0f}); re-tracked and kept {bpm:.1f} BPM."
        )
    beats = extend_back(beats)
    beat_frames = librosa.time_to_frames(np.asarray(beats), sr=sr)
    phase_low = downbeat_phase(beat_frames, low_env)
    start = first_sound(y, sr)
    period = 60.0 / bpm
    if start is not None and start <= 0.05 and abs(beats[0] - start) <= period / 4:
        phase = 0
        rule = "bar 1 = first beat (the bounce starts with sound on a beat, as a DAW export from bar 1 does)"
        if phase_low != 0:
            notes.append(
                f"Kick/bass energy alone would put beat 1 on beat {phase_low + 1} of the grid. "
                "If bar numbers feel off by a beat or two, the bounce did not start at bar 1."
            )
    else:
        phase = phase_low
        rule = "beat 1 chosen from kick/bass (<250 Hz) onset strength (bounce does not start on a beat)"
    downbeats = beats[phase::BEATS_PER_BAR]
    level, note = _confidence(beats, corrected)
    return TempoResult(
        bpm=round(bpm, 1),
        beats=beats,
        downbeats=downbeats,
        raw_bpm=round(raw, 1),
        octave_corrected=corrected,
        confidence=level,
        confidence_note=note,
        downbeat_rule=rule,
        notes=notes,
    )
