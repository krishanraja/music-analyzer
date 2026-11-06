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
- Fixed-grid refinement: a DAW bounce has one exact tempo, so trackcard
  scores every steady grid (BPM within +/-2 of the estimate, every phase)
  against the onset envelope across the whole file. When one grid stands out
  clearly (best score at least 1.5x the median), that grid replaces the
  tracked beats and gives the BPM to 0.01. Otherwise (live playing, tempo
  changes) the tracked beats stay, and BPM is the slope of a straight-line fit
  through them.
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
    grid: str = "tracked"  # "fixed" | "tracked" | "producer"
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


GRID_SHARPNESS = 1.5
GRID_SEARCH_BPM = 2.0
HOP = 512


def _grid_scores(env: np.ndarray, frame_rate: float, n_frames: int, bpms: np.ndarray, phase_step: float = 0.005):
    """For each BPM, the best mean onset strength over all grid phases. Returns (scores, phases)."""
    duration = n_frames / frame_rate
    scores, phases = np.zeros(len(bpms)), np.zeros(len(bpms))
    for i, bpm in enumerate(bpms):
        period = 60.0 / bpm
        ph = np.arange(0.0, period, phase_step)
        k = np.arange(int(duration / period) + 1)
        t = ph[:, None] + k[None, :] * period
        fr = np.round(t * frame_rate).astype(int)
        valid = fr < n_frames
        vals = np.where(valid, env[np.minimum(fr, n_frames - 1)], 0.0)
        mean = vals.sum(1) / np.maximum(valid.sum(1), 1)
        j = int(np.argmax(mean))
        scores[i], phases[i] = mean[j], ph[j]
    return scores, phases


def refine_fixed_grid(env: np.ndarray, sr: int, bpm_estimate: float, fixed_bpm: float | None = None):
    """Fit one steady grid to the whole file.

    Returns (bpm, phase_seconds, sharpness) or None if no grid stands out.
    With fixed_bpm, only the phase is searched and the result is always returned."""
    from scipy.ndimage import maximum_filter1d

    raw = np.asarray(env, dtype=float)
    env = maximum_filter1d(raw, size=3)  # tolerate +/-1 frame
    fps = sr / HOP
    n = len(env)
    if fixed_bpm is not None:
        _, ph = _grid_scores(env, fps, n, np.array([fixed_bpm]))
        return float(fixed_bpm), float(ph[0]), float("nan")
    coarse = np.arange(bpm_estimate - GRID_SEARCH_BPM, bpm_estimate + GRID_SEARCH_BPM + 1e-9, 0.05)
    sc, _ = _grid_scores(env, fps, n, coarse, phase_step=0.01)
    best_i = int(np.argmax(sc))
    sharp = float(sc[best_i] / (np.median(sc) + 1e-9))
    if sharp < GRID_SHARPNESS:
        return None
    fine = np.arange(coarse[best_i] - 0.06, coarse[best_i] + 0.06 + 1e-9, 0.005)
    fsc, fph = _grid_scores(env, fps, n, fine)
    ties = np.flatnonzero(fsc >= fsc.max() * 0.999)  # clean audio can tie on a plateau
    j = int(ties[len(ties) // 2])
    bpm, phase = _regress_on_peaks(raw, fps, float(fine[j]), float(fph[j]))
    return bpm, phase, sharp


def _regress_on_peaks(env: np.ndarray, fps: float, bpm: float, phase: float, radius: int = 3):
    """Snap each grid beat to the onset peak within +/-radius frames (strong beats only)
    and fit a straight line, removing the bias of a flat-topped score."""
    period = 60.0 / bpm
    k = np.arange(int((len(env) / fps - phase) / period) + 1)
    fr = np.round((phase + k * period) * fps).astype(int)
    keep = fr < len(env)
    k, fr = k[keep], fr[keep]
    peaks, strength = [], []
    for f in fr:
        lo, hi = max(f - radius, 0), min(f + radius + 1, len(env))
        i = lo + int(np.argmax(env[lo:hi]))
        peaks.append(i / fps)
        strength.append(env[i])
    peaks, strength = np.asarray(peaks), np.asarray(strength)
    strong = strength >= np.median(strength)
    if strong.sum() < 8:
        return bpm, phase
    slope, icpt = np.polyfit(k[strong], peaks[strong], 1)
    if slope <= 0 or abs(60.0 / slope - bpm) > 0.5:
        return bpm, phase
    return 60.0 / slope, float(icpt % slope)


def grid_beats(bpm: float, phase: float, duration: float) -> list[float]:
    period = 60.0 / bpm
    return [float(t) for t in np.arange(phase, duration, period)]


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


def analyze_tempo(y: np.ndarray, sr: int, bpm_override: float | None = None) -> TempoResult:
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
    duration = len(y) / sr
    grid = "tracked"
    sharp = float("nan")
    fit = refine_fixed_grid(onset_env, sr, bpm, fixed_bpm=bpm_override)
    if fit is not None:
        bpm, phase0, sharp = fit
        beats = grid_beats(bpm, phase0, duration)
        grid = "producer" if bpm_override is not None else "fixed"
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
    if grid == "fixed":
        level = "high"
        note = (f"one steady tempo fits the whole track (grid score {sharp:.1f}x the typical "
                "neighbouring tempo), as in a DAW bounce")
        if corrected:
            note += "; tempo was octave-corrected, so half or double time is possible"
            level = "medium"
    elif grid == "producer":
        level, note = "set by producer", "grid fitted at the tempo the producer gave"
    else:
        level, note = _confidence(beats, corrected)
        note += "; no single steady grid fits the whole file (tempo may drift or change)"
    return TempoResult(
        bpm=round(bpm, 2),
        beats=beats,
        downbeats=downbeats,
        raw_bpm=round(raw, 1),
        octave_corrected=corrected,
        confidence=level,
        confidence_note=note,
        downbeat_rule=rule,
        grid=grid,
        notes=notes,
    )
