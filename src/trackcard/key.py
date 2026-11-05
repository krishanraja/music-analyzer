"""Key and mode via Krumhansl-Schmuckler, with Camelot output.

Method (reimplemented from the approach published in MixMind): harmonic-
percussive separation, chroma_cens on the harmonic part, RMS-weighted average
chroma, correlation against the 24 Krumhansl-Kessler profiles, and a relative
major/minor tiebreak when the two correlations are within 0.05 (the louder
tonic in the chroma wins).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
CAMELOT_MAJOR = ["8B", "3B", "10B", "5B", "12B", "7B", "2B", "9B", "4B", "11B", "6B", "1B"]
CAMELOT_MINOR = ["5A", "12A", "7A", "2A", "9A", "4A", "11A", "6A", "1A", "8A", "3A", "10A"]
RELATIVE_TIE = 0.05


@dataclass
class KeyResult:
    tonic: str
    mode: str  # "major" | "minor"
    camelot: str
    score: float
    margin: float  # correlation gap to the runner-up key
    runner_up: str
    mode_tiebreak_used: bool

    @property
    def name(self) -> str:
        return f"{self.tonic} {self.mode}"

    @property
    def confidence(self) -> str:
        if self.margin >= 0.08 and self.score >= 0.6:
            return "high"
        if self.margin >= 0.03:
            return "medium"
        return "low"


def _label(p: int, major: bool) -> str:
    return f"{PITCHES[p]} {'major' if major else 'minor'}"


def compatible_camelot(code: str) -> list[str]:
    """Same code, +/-1 on the wheel, and the relative major/minor."""
    n, lane = int(code[:-1]), code[-1]
    up = (n % 12) + 1
    down = ((n - 2) % 12) + 1
    other = "A" if lane == "B" else "B"
    return [code, f"{up}{lane}", f"{down}{lane}", f"{n}{other}"]


def key_from_chroma(chroma: np.ndarray) -> KeyResult:
    if chroma.shape != (12,):
        raise ValueError(f"chroma must have shape (12,), got {chroma.shape}")
    scores: dict[tuple[int, bool], float] = {}
    for s in range(12):
        rot = np.roll(chroma, -s)
        scores[(s, True)] = float(np.corrcoef(rot, MAJOR)[0, 1])
        scores[(s, False)] = float(np.corrcoef(rot, MINOR)[0, 1])
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (p, maj), best = ranked[0]
    rel = ((p + 9) % 12, False) if maj else ((p + 3) % 12, True)
    flipped = False
    if best - scores[rel] < RELATIVE_TIE and chroma[rel[0]] > chroma[p]:
        p, maj = rel
        flipped = True
    chosen = scores[(p, maj)]
    others = [(k, v) for k, v in ranked if k != (p, maj)]
    (rp, rmaj), rscore = others[0]
    return KeyResult(
        tonic=PITCHES[p],
        mode="major" if maj else "minor",
        camelot=(CAMELOT_MAJOR if maj else CAMELOT_MINOR)[p],
        score=round(chosen, 3),
        margin=round(max(chosen - rscore, 0.0), 3),
        runner_up=_label(rp, rmaj),
        mode_tiebreak_used=flipped,
    )


def analyze_key(y: np.ndarray, sr: int) -> KeyResult:
    import librosa

    yh = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cens(y=yh, sr=sr)
    rms = librosa.feature.rms(y=yh)[0]
    n = min(len(rms), chroma.shape[1])
    w = rms[:n] / (rms[:n].sum() + 1e-9)
    return key_from_chroma((chroma[:, :n] * w[None, :]).sum(axis=1))
