"""Key and mode via Krumhansl-Schmuckler, with Camelot output.

Method (reimplemented from the approach published in MixMind): harmonic-
percussive separation, chroma_cens on the harmonic part, RMS-weighted average
chroma, correlation against the 24 Krumhansl-Kessler profiles, and a relative
major/minor tiebreak when the two correlations are within 0.05 (the louder
tonic in the chroma wins).

Added in trackcard: a second opinion from Temperley's (1999) key profiles.
If Temperley ranks a closely related key (sharing at least 6 of 7 scale
notes) in its top two and within 0.1 of its top score, that key is named as
an alternative; failing that, if the Krumhansl-Schmuckler runner-up is within
0.1, it is named instead. Riffs that lean on the 4th and 5th can make averaged harmony favour
a neighbouring key.
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
TEMPERLEY_MAJOR = np.array([5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0])
TEMPERLEY_MINOR = np.array([5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0])
ALT_KK_MARGIN = 0.1
ALT_TEMPERLEY_GAP = 0.1
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


@dataclass
class KeyResult:
    tonic: str
    mode: str  # "major" | "minor"
    camelot: str
    score: float
    margin: float  # correlation gap to the runner-up key
    runner_up: str
    mode_tiebreak_used: bool
    alternative: str | None = None
    alternative_camelot: str | None = None
    alternative_reason: str = ""

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


def scale_notes(pitch: int, major: bool) -> set[int]:
    return {(pitch + i) % 12 for i in (MAJOR_SCALE if major else MINOR_SCALE)}


def camelot_of(pitch: int, major: bool) -> str:
    return (CAMELOT_MAJOR if major else CAMELOT_MINOR)[pitch]


def _rank(chroma: np.ndarray, maj_prof: np.ndarray, min_prof: np.ndarray):
    sc = {}
    for s in range(12):
        rot = np.roll(chroma, -s)
        sc[(s, True)] = float(np.corrcoef(rot, maj_prof)[0, 1])
        sc[(s, False)] = float(np.corrcoef(rot, min_prof)[0, 1])
    return sorted(sc.items(), key=lambda kv: kv[1], reverse=True)


# enharmonic spellings mapped onto PITCHES (which uses sharps)
FLATS = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#", "CB": "B", "FB": "E", "E#": "F", "B#": "C"}


def parse_key(text: str) -> tuple[int, bool]:
    """Parse "D minor", "Dm", "D min", "F# major", "Bb", "Bbm", "7A". Returns (pitch, is_major)."""
    t = text.strip()
    up = t.upper().replace(" ", "")
    if up[:-1].isdigit() and up[-1] in "AB":
        n = int(up[:-1])
        if 1 <= n <= 12:
            code = f"{n}{up[-1]}"
            if up[-1] == "B":
                return CAMELOT_MAJOR.index(code), True
            return CAMELOT_MINOR.index(code), False
    import re

    m = re.fullmatch(r"([A-Ga-g])([#b]?)\s*(major|maj|minor|min|m|M)?", t)
    if not m:
        raise ValueError(
            f"Could not read the key '{text}'. Use forms like 'D minor', 'Dm', 'F# major', 'Bb' or a Camelot code like '7A'."
        )
    letter, acc = m.group(1).upper(), m.group(2)
    if acc == "b":
        note = FLATS[f"{letter}B"] if f"{letter}B" in FLATS else letter
    elif acc == "#":
        note = FLATS.get(f"{letter}#", f"{letter}#")
    else:
        note = letter
    q = m.group(3)
    major = not (q in ("minor", "min", "m"))
    return PITCHES.index(note), major


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
    margin = max(chosen - rscore, 0.0)
    alt, alt_reason = None, ""
    shared = scale_notes(p, maj)
    # 1) an independent second profile picks a closely related key
    trank = _rank(chroma, TEMPERLEY_MAJOR, TEMPERLEY_MINOR)
    ttop = trank[0][1]
    for (tp, tmaj), tsc in trank[:2]:
        if (tp, tmaj) == (p, maj) or ttop - tsc > ALT_TEMPERLEY_GAP:
            continue
        common = len(shared & scale_notes(tp, tmaj))
        if common >= 6:
            alt = (tp, tmaj)
            alt_reason = (f"they share {common} of 7 notes and a second key profile "
                          f"(Temperley) ranks {_label(tp, tmaj)} {'first' if tsc == ttop else 'close behind'}")
            break
    # 2) otherwise, a near tie in the main method
    if alt is None and margin < ALT_KK_MARGIN:
        alt = (rp, rmaj)
        alt_reason = f"the two keys score within {margin:.2f} of each other"
    return KeyResult(
        tonic=PITCHES[p],
        mode="major" if maj else "minor",
        camelot=(CAMELOT_MAJOR if maj else CAMELOT_MINOR)[p],
        score=round(chosen, 3),
        margin=round(margin, 3),
        runner_up=_label(rp, rmaj),
        mode_tiebreak_used=flipped,
        alternative=_label(*alt) if alt else None,
        alternative_camelot=camelot_of(*alt) if alt else None,
        alternative_reason=alt_reason,
    )


def analyze_key(y: np.ndarray, sr: int) -> KeyResult:
    import librosa

    yh = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cens(y=yh, sr=sr)
    rms = librosa.feature.rms(y=yh)[0]
    n = min(len(rms), chroma.shape[1])
    w = rms[:n] / (rms[:n].sum() + 1e-9)
    return key_from_chroma((chroma[:, :n] * w[None, :]).sum(axis=1))
