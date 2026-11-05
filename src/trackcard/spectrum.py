"""Frequency balance per section, at the file's native sample rate."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BANDS: list[tuple[str, float, float]] = [
    ("sub", 20.0, 60.0),
    ("low", 60.0, 250.0),
    ("mid", 250.0, 2000.0),
    ("high-mid", 2000.0, 8000.0),
    ("air", 8000.0, 20000.0),
]
BAND_NAMES = [b[0] for b in BANDS]

# Reference shape: roughly how a full, balanced electronic mix distributes
# energy across these bands, in dB relative to the loudest band. Used only to
# phrase flags ("thin", "missing"), not as a target to mix to.
EMPTY_DB = -45.0  # band this far below the loudest band = effectively nothing there
THIN_DB = {"sub": -24.0, "low": -15.0, "mid": -18.0, "high-mid": -24.0, "air": -33.0}
HEAVY_GAP_DB = 3.0  # sub louder than low by this much = sub-heavy


@dataclass
class BandProfile:
    start: float
    end: float
    db: dict[str, float]  # absolute band level, dBFS-ish (mean power in band)
    rel_db: dict[str, float]  # relative to loudest band in this span
    flags: list[str] = field(default_factory=list)
    available: dict[str, bool] = field(default_factory=dict)


def _band_power(x: np.ndarray, sr: int) -> tuple[dict[str, float], dict[str, bool]]:
    from scipy.signal import welch

    nyq = sr / 2.0
    f, pxx = welch(x, fs=sr, nperseg=min(8192, len(x)))
    out, avail = {}, {}
    for name, lo, hi in BANDS:
        hi_eff = min(hi, nyq)
        avail[name] = hi_eff > lo * 1.25
        m = (f >= lo) & (f < hi_eff)
        p = float(np.sum(pxx[m]) * (f[1] - f[0])) if m.any() else 0.0
        out[name] = 10.0 * np.log10(p + 1e-12)
    return out, avail


def flags_for(rel: dict[str, float], avail: dict[str, bool], label: str = "") -> list[str]:
    """Plain-language flags. With a label, each flag ends "in the <label>"."""
    where = f" in the {label}" if label else ""
    f = []
    if not avail.get("air", True):
        f.append("file sample rate too low to judge the air band")
    elif rel["air"] <= EMPTY_DB and rel["high-mid"] <= EMPTY_DB:
        f.append(f"nothing above 2 kHz{where}")
    elif rel["air"] <= EMPTY_DB:
        f.append(f"nothing above 8 kHz{where}")
    elif rel["air"] <= THIN_DB["air"]:
        f.append(f"very little air (8 kHz+){where}")
    if rel["sub"] <= EMPTY_DB:
        f.append(f"no sub (under 60 Hz){where}")
    elif rel["sub"] <= THIN_DB["sub"]:
        f.append(f"light on sub{where}")
    if rel["low"] <= THIN_DB["low"]:
        f.append(f"thin low end (60-250 Hz){where}")
    if rel["sub"] - rel["low"] >= HEAVY_GAP_DB:
        f.append(f"sub-heavy: sub sits {rel['sub'] - rel['low']:.0f} dB over the low band")
    if rel["mid"] <= THIN_DB["mid"]:
        f.append(f"mids are scooped (250 Hz-2 kHz){where}")
    if rel["high-mid"] <= THIN_DB["high-mid"] and rel["air"] > EMPTY_DB:
        f.append(f"dull presence range (2-8 kHz){where}")
    return f


def band_profile(mono: np.ndarray, sr: int, start: float, end: float, label: str = "") -> BandProfile:
    a, b = int(start * sr), int(end * sr)
    seg = mono[a:b]
    if len(seg) < 2048:
        seg = np.pad(seg, (0, 2048 - len(seg)))
    absdb, avail = _band_power(seg, sr)
    top = max(absdb[n] for n in BAND_NAMES if avail[n])
    rel = {n: round(absdb[n] - top, 1) for n in BAND_NAMES}
    return BandProfile(
        start=start,
        end=end,
        db={k: round(v, 1) for k, v in absdb.items()},
        rel_db=rel,
        flags=flags_for(rel, avail, label),
        available=avail,
    )
