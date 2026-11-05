"""Section map.

1. allin1 (Kim & Nam's All-In-One music structure model), when installed:
   functional labels such as intro, verse, chorus, break, drop, outro.
2. Fallback: Laplacian structural segmentation (McFee & Ellis, 2014) as
   published in MixMind's approach: beat-synchronous CQT, recurrence matrix,
   path enhancement, spectral clustering on the normalised Laplacian with k
   chosen by eigengap in [3, 6], and sections under 8 s merged into a
   neighbour. trackcard also adds an MFCC timbre path to the graph (as in the
   original McFee & Ellis method) because electronic sections often differ in
   timbre and rhythm more than in pitch. Labels are section_N: the same N means
   the same kind of material, not a functional name.

Boundaries are snapped to the nearest downbeat when one is within half a bar,
then to the nearest 4-bar phrase start (bars 1, 5, 9, ...) when one is within
one bar, because dance music sections almost always start on a phrase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

K_MIN, K_MAX = 3, 6
MIN_SECTION_S = 8.0
MEDIAN_FILTER = 15


@dataclass
class Section:
    start: float
    end: float
    label: str

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class SectionMap:
    sections: list[Section]
    method: str  # "allin1" | "laplacian" | "single"
    notes: list[str] = field(default_factory=list)
    # allin1 also predicts beats; when it ran, these replace the librosa grid.
    bpm: float | None = None
    beats: list[float] | None = None
    downbeats: list[float] | None = None


# ---------------------------------------------------------------- allin1


def run_allin1(path: Path) -> tuple[SectionMap | None, str]:
    """Returns (map, reason). map is None when allin1 is missing or fails."""
    try:
        import allin1  # type: ignore
    except Exception as exc:  # ImportError, or a broken native dependency
        return None, f"allin1 is not installed ({type(exc).__name__}: {exc})"
    try:
        r = allin1.analyze(str(path))
    except Exception as exc:
        return None, f"allin1 failed on this file ({type(exc).__name__}: {exc})"
    segs = [Section(float(s.start), float(s.end), str(s.label)) for s in (r.segments or [])]
    segs = [s for s in segs if s.end > s.start]
    if not segs or not r.beats or not r.downbeats or not r.bpm:
        return None, "allin1 returned empty or degenerate output"
    return (
        SectionMap(
            sections=segs,
            method="allin1",
            bpm=float(r.bpm),
            beats=[float(b) for b in r.beats],
            downbeats=[float(b) for b in r.downbeats],
        ),
        "",
    )


# ---------------------------------------------------------------- laplacian


def choose_k(eigvals: np.ndarray, k_min: int = K_MIN, k_max: int = K_MAX) -> int:
    gaps = np.diff(eigvals)
    lo, hi = k_min - 1, min(k_max - 1, len(gaps) - 1)
    if hi < lo:
        return k_min
    return int(np.argmax(gaps[lo : hi + 1])) + lo + 1


def laplacian_segments(y: np.ndarray, sr: int, beat_times: list[float]) -> list[Section]:
    import librosa
    import scipy.ndimage
    import scipy.signal
    from scipy.sparse.csgraph import laplacian
    from sklearn.cluster import KMeans

    duration = len(y) / sr
    whole = [Section(0.0, duration, "section_1")]
    beats = librosa.time_to_frames(np.asarray(beat_times), sr=sr)
    beats = beats[beats < 1 + len(y) // 512]
    if len(beats) < K_MAX * 4:
        return whole

    cqt = librosa.amplitude_to_db(
        np.abs(librosa.cqt(y=y, sr=sr, bins_per_octave=36, n_bins=7 * 36)), ref=np.max
    )
    csync = librosa.util.sync(cqt, beats, aggregate=np.median)
    rec = librosa.segment.recurrence_matrix(csync, width=3, mode="affinity", sym=True)
    rec = librosa.segment.path_enhance(rec, 15)

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    msync = librosa.util.sync(mfcc, beats)
    dist = np.sum(np.diff(msync, axis=1) ** 2, axis=0)
    sigma = np.median(dist) + 1e-9
    path_sim = np.exp(-dist / sigma)
    path = np.diag(path_sim, k=1) + np.diag(path_sim, k=-1)

    deg_path = path.sum(axis=1)
    deg_rec = rec.sum(axis=1)
    mu = deg_path.dot(deg_path + deg_rec) / (np.sum((deg_path + deg_rec) ** 2) + 1e-9)
    graph = mu * rec + (1 - mu) * path
    if np.any(graph.sum(axis=1) == 0):
        graph = graph + 1e-6

    L = laplacian(graph, normed=True)
    eigvals, eigvecs = np.linalg.eigh(L)
    eigvecs = scipy.ndimage.median_filter(eigvecs, size=(9, 1))
    k = choose_k(eigvals[: K_MAX + 1])
    emb = eigvecs[:, :k]
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(emb / norms)
    labels = scipy.signal.medfilt(labels.astype(float), MEDIAN_FILTER).astype(int)

    # beat-synchronous frame i spans [bound_times[i], bound_times[i+1])
    bound_frames = librosa.util.fix_frames(beats, x_min=0)
    bt = librosa.frames_to_time(bound_frames, sr=sr)
    bt = list(bt) + [duration]
    secs: list[Section] = []
    for i, lab in enumerate(labels):
        s, e = float(bt[i]), float(bt[i + 1]) if i + 1 < len(bt) else duration
        name = f"section_{lab + 1}"
        if secs and secs[-1].label == name:
            secs[-1].end = e
        else:
            secs.append(Section(s, e, name))
    secs[0].start = 0.0
    secs[-1].end = duration
    secs = [s for s in secs if s.end > s.start]
    return renumber(merge_short(secs, MIN_SECTION_S))


def _coalesce(secs: list[Section]) -> list[Section]:
    out: list[Section] = []
    for s in secs:
        if out and out[-1].label == s.label and abs(out[-1].end - s.start) < 1e-6:
            out[-1] = Section(out[-1].start, s.end, out[-1].label)
        else:
            out.append(Section(s.start, s.end, s.label))
    return out


def merge_short(secs: list[Section], min_s: float) -> list[Section]:
    """Absorb sections shorter than min_s into the longer neighbour (taking its label)."""
    secs = _coalesce(secs)
    while len(secs) > 1:
        i = min(range(len(secs)), key=lambda j: secs[j].length)
        if secs[i].length >= min_s:
            break
        if i == 0:
            t = 1
        elif i == len(secs) - 1:
            t = i - 1
        else:
            t = i - 1 if secs[i - 1].length >= secs[i + 1].length else i + 1
        a, b = min(i, t), max(i, t)
        merged = Section(secs[a].start, secs[b].end, secs[t].label)
        secs = _coalesce(secs[:a] + [merged] + secs[b + 1 :])
    return secs


def renumber(secs: list[Section]) -> list[Section]:
    """Relabel section_N in order of first appearance, so the first section is section_1."""
    mapping: dict[str, str] = {}
    for s in secs:
        if s.label not in mapping:
            mapping[s.label] = f"section_{len(mapping) + 1}"
    return [Section(s.start, s.end, mapping[s.label]) for s in secs]


def snap_to_downbeats(secs: list[Section], downbeats: list[float], bar_s: float) -> list[Section]:
    if not downbeats or len(secs) < 2:
        return secs
    db = np.asarray(downbeats)
    out = [Section(s.start, s.end, s.label) for s in secs]
    for i in range(1, len(out)):
        t = out[i].start
        j = int(np.argmin(np.abs(db - t)))
        if abs(db[j] - t) <= bar_s / 2 and out[i - 1].start < db[j] < out[i].end:
            out[i - 1].end = float(db[j])
            out[i].start = float(db[j])
    return out


def snap_to_phrases(secs: list[Section], downbeats: list[float], phrase_bars: int = 4) -> tuple[list[Section], int]:
    """Move each boundary to the nearest phrase-start downbeat if it is at most one bar away.
    Returns (sections, number of boundaries moved)."""
    if len(downbeats) < phrase_bars * 2 or len(secs) < 2:
        return secs, 0
    db = np.asarray(downbeats)
    phrase_starts = db[::phrase_bars]
    out = [Section(s.start, s.end, s.label) for s in secs]
    moved = 0
    for i in range(1, len(out)):
        t = out[i].start
        bar_idx = int(np.argmin(np.abs(db - t)))
        if abs(db[bar_idx] - t) > 0.05:  # not on a downbeat: leave alone
            continue
        ph = int(np.argmin(np.abs(phrase_starts - t)))
        target = float(phrase_starts[ph])
        if target == t or abs(ph * phrase_bars - bar_idx) > 1:
            continue
        if out[i - 1].start < target < out[i].end:
            out[i - 1].end = target
            out[i].start = target
            moved += 1
    return out, moved


def detect_sections(
    path: Path, y: np.ndarray, sr: int, beat_times: list[float], mode: str = "auto"
) -> SectionMap:
    """mode: "auto" (allin1 then laplacian), "allin1" (allin1 only, error if missing),
    or "laplacian"."""
    notes: list[str] = []
    if mode in ("auto", "allin1"):
        m, reason = run_allin1(path)
        if m is not None:
            return m
        if mode == "allin1":
            from .audio import TrackcardError

            raise TrackcardError(f"--sections allin1 was requested but {reason}.")
        notes.append(
            f"Section labels fell back to librosa Laplacian segmentation because {reason}. "
            "Labels are section_N (same N = similar material), not intro/verse/drop."
        )
    secs = laplacian_segments(y, sr, beat_times)
    method = "laplacian"
    if len(secs) == 1:
        notes.append("Structural segmentation found a single section (too few beats or no repeats).")
        method = "single"
    return SectionMap(sections=secs, method=method, notes=notes)
