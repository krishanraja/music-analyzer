"""Loudness and dynamics across the timeline."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LoudnessResult:
    peak_dbfs: float
    rms_dbfs: float
    crest_db: float
    integrated_lufs: float | None
    lufs_method: str
    per_second_dbfs: list[float]
    clipped_samples: int
    notes: list[str] = field(default_factory=list)


def db(x: float) -> float:
    return float(20.0 * np.log10(max(x, 1e-9)))


def rms_dbfs(x: np.ndarray) -> float:
    return db(float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))) if len(x) else -180.0


def analyze_loudness(native: np.ndarray, sr: int) -> LoudnessResult:
    mono = native.mean(axis=1)
    peak = float(np.max(np.abs(native)))
    rms = rms_dbfs(mono)
    clipped = int(np.sum(np.abs(native) >= 0.999))
    notes: list[str] = []
    lufs, method = None, "not measured"
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sr)
        val = float(meter.integrated_loudness(native.astype(np.float64)))
        if np.isfinite(val):
            lufs, method = round(val, 1), "ITU-R BS.1770 via pyloudnorm"
    except ImportError:
        notes.append(
            "Integrated LUFS not measured: pyloudnorm is not installed "
            "(pip install 'trackcard[loudness]'). RMS dBFS is reported instead."
        )
    except Exception as exc:
        notes.append(f"Integrated LUFS failed ({exc}); RMS dBFS is reported instead.")
    n = int(np.ceil(len(mono) / sr))
    per_sec = [round(rms_dbfs(mono[i * sr : (i + 1) * sr]), 1) for i in range(n)]
    if clipped > 0:
        notes.append(f"{clipped} samples hit 0 dBFS; the bounce may be clipping.")
    return LoudnessResult(
        peak_dbfs=round(db(peak), 1),
        rms_dbfs=round(rms, 1),
        crest_db=round(db(peak) - rms, 1),
        integrated_lufs=lufs,
        lufs_method=method,
        per_second_dbfs=per_sec,
        clipped_samples=clipped,
        notes=notes,
    )


def span_rms_dbfs(native: np.ndarray, sr: int, start: float, end: float) -> float:
    return round(rms_dbfs(native[int(start * sr) : int(end * sr)].mean(axis=1)), 1)
