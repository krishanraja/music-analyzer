"""Audio loading with plain-language errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

ANALYSIS_SR = 22050
MIN_DURATION_S = 5.0
SUPPORTED_SUFFIXES = {".wav", ".wave", ".aif", ".aiff", ".flac"}


class TrackcardError(Exception):
    """An error with a message meant to be read by a person."""


@dataclass
class LoadedAudio:
    path: Path
    y: np.ndarray  # mono float32 at ANALYSIS_SR (beats, key, sections)
    sr: int
    native: np.ndarray  # (samples, channels) float32 at native_sr (spectrum, loudness)
    native_sr: int
    channels: int
    duration: float


def load_audio(path: str | Path) -> LoadedAudio:
    import librosa
    import soundfile as sf

    p = Path(path).expanduser()
    if not p.exists():
        raise TrackcardError(f"File not found: {p}")
    if p.is_dir():
        raise TrackcardError(f"{p} is a folder. Point trackcard at a single WAV bounce.")
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise TrackcardError(
            f"{p.name}: unsupported file type '{p.suffix}'. Export a WAV bounce "
            f"(supported: {', '.join(sorted(SUPPORTED_SUFFIXES))})."
        )
    try:
        info = sf.info(str(p))
    except Exception as exc:  # soundfile raises RuntimeError / LibsndfileError
        raise TrackcardError(f"Could not read {p.name} as audio: {exc}") from exc
    if info.duration < MIN_DURATION_S:
        raise TrackcardError(
            f"{p.name} is {info.duration:.1f} s long. trackcard needs at least "
            f"{MIN_DURATION_S:.0f} s to find a tempo and structure."
        )
    native, native_sr = sf.read(str(p), always_2d=True, dtype="float32")
    mono = native.mean(axis=1)
    if not np.any(np.abs(mono) > 1e-4):
        raise TrackcardError(f"{p.name} is silent (no samples above -80 dBFS).")
    y = librosa.resample(mono, orig_sr=native_sr, target_sr=ANALYSIS_SR)
    sr = ANALYSIS_SR
    return LoadedAudio(
        path=p,
        y=y.astype(np.float32),
        sr=sr,
        native=native,
        native_sr=int(native_sr),
        channels=int(info.channels),
        duration=float(len(y) / sr),
    )
