"""Synthetic audio for tests. Everything is generated; no audio files are committed."""

from __future__ import annotations

import numpy as np

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def kick(sr=SR, dur=0.25):
    t = np.arange(int(sr * dur)) / sr
    f = 50 + 100 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 12)


def click(sr=SR, dur=0.03):
    t = np.arange(int(sr * dur)) / sr
    return np.random.default_rng(0).standard_normal(len(t)) * np.exp(-t * 200)


def hat(sr=SR, dur=0.05):
    from scipy.signal import butter, sosfilt

    n = np.random.default_rng(1).standard_normal(int(sr * dur))
    sos = butter(4, 8000, "highpass", fs=sr, output="sos")
    return sosfilt(sos, n) * np.exp(-np.arange(len(n)) / sr * 80)


def place(buf, sample, t, sr=SR, gain=1.0):
    a = int(t * sr)
    b = min(len(buf), a + len(sample))
    if a < len(buf):
        buf[a:b] += sample[: b - a] * gain


def click_track(bpm: float, seconds: float, sr=SR, accent_downbeat=True):
    """Kick on every beat (louder on beat 1), clicks on 8ths."""
    buf = np.zeros(int(seconds * sr))
    beat = 60.0 / bpm
    i = 0
    while i * beat < seconds:
        g = 1.0 if (i % 4 == 0 or not accent_downbeat) else 0.6
        place(buf, kick(sr), i * beat, sr, g)
        place(buf, click(sr), i * beat + beat / 2, sr, 0.3)
        i += 1
    return buf / np.max(np.abs(buf)) * 0.8


def chord(notes_midi, dur, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    env = np.minimum(1.0, t * 20) * np.minimum(1.0, (dur - t) * 20)
    out = np.zeros_like(t)
    for m in notes_midi:
        for h, a in ((1, 1.0), (2, 0.4), (3, 0.2)):
            out += a * np.sin(2 * np.pi * midi_hz(m) * h * t)
    return out * env


def triad(root: str, minor: bool, octave=4):
    r = 12 * (octave + 1) + NOTE[root]
    return [r, r + (3 if minor else 4), r + 7]


def progression(chords, chord_dur=2.0, repeats=3, sr=SR):
    parts = [chord(triad(r, m), chord_dur, sr) for _ in range(repeats) for r, m in chords]
    y = np.concatenate(parts)
    return y / np.max(np.abs(y)) * 0.6


def bass_line(root: str, bpm: float, seconds: float, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    m = 12 * 2 + NOTE[root]
    beat = 60.0 / bpm
    gate = ((t % beat) < beat * 0.7).astype(float)
    return np.sin(2 * np.pi * midi_hz(m) * t) * gate


def three_part_track(bpm=124.0, sr=SR):
    """A (16 bars: pad only) / B (16 bars: drums + bass + pad + hats) / A' (16 bars: pad only).

    Returns (stereo audio, boundaries in seconds)."""
    bar = 4 * 60.0 / bpm
    seg = 16 * bar
    total = 3 * seg
    pad = progression([("A", True), ("F", False), ("C", False), ("G", False)], chord_dur=bar, repeats=12, sr=sr)
    pad = np.pad(pad, (0, max(0, int(total * sr) - len(pad))))[: int(total * sr)] * 0.5
    drums = np.zeros(int(total * sr))
    beat = 60.0 / bpm
    i = 0
    while i * beat < seg:
        place(drums, kick(sr), seg + i * beat, sr, 1.0)
        place(drums, hat(sr), seg + i * beat + beat / 2, sr, 0.5)
        i += 1
    bass = np.zeros(int(total * sr))
    bl = bass_line("A", bpm, seg, sr)
    bass[int(seg * sr) : int(seg * sr) + len(bl)] = bl * 0.5
    y = pad + drums + bass
    y = y / np.max(np.abs(y)) * 0.8
    return np.stack([y, y], axis=1).astype(np.float32), (seg, 2 * seg)
