"""Stem separation with Demucs htdemucs, plus 10 Hz activity envelopes.

The Demucs approach (separate a bounce into drums, bass, vocals, other) comes
from Stem MCP; the device fallback chain and 10 Hz RMS/peak envelopes follow
the approach published in MixMind. Code here is written for trackcard.

Device chain: GPU (CUDA or Apple MPS) with shifts=2, then the same GPU with
shifts=0 if memory runs out, then CPU with shifts=0. Without a GPU it starts
on CPU with shifts=0 (shifts multiply CPU time and are not worth it there).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

STEMS = ("drums", "bass", "vocals", "other")
# The drums stem's total level is dominated by the kick, so hats and cymbals
# returning can't be seen in it. Two extra envelopes split it by band.
DRUM_BANDS = {"kick": (None, 150.0), "tops": (5000.0, None)}
FRAME_HZ = 10
MODEL_NAME = "htdemucs"


@dataclass
class StemResult:
    available: bool
    frame_hz: int = FRAME_HZ
    envelopes: dict[str, dict[str, list[float]]] = field(default_factory=dict)  # stem -> {rms, peak}
    device: str = ""
    shifts: int = 0
    notes: list[str] = field(default_factory=list)


def compute_envelopes(stems: dict[str, np.ndarray], sr: int, frame_hz: int = FRAME_HZ) -> dict:
    """stems: name -> (channels, samples). Returns name -> {"rms": [...], "peak": [...]}."""
    out = {}
    size = max(1, sr // frame_hz)
    for name, x in stems.items():
        if x.ndim != 2:
            raise ValueError(f"{name}: expected (channels, samples), got {x.shape}")
        mono = x.mean(axis=0)
        n = len(mono) // size
        if n == 0:
            out[name] = {"rms": [], "peak": []}
            continue
        fr = mono[: n * size].reshape(n, size)
        out[name] = {
            "rms": np.sqrt(np.mean(fr.astype(np.float64) ** 2, axis=1)).astype(float).tolist(),
            "peak": np.abs(fr).max(axis=1).astype(float).tolist(),
        }
    return out


def drum_bands(drums: np.ndarray, sr: int) -> dict[str, np.ndarray]:
    """Split the drums stem (channels, samples) into kick (<150 Hz) and tops (>5 kHz)."""
    from scipy.signal import butter, sosfiltfilt

    out = {}
    for name, (lo, hi) in DRUM_BANDS.items():
        if lo is None:
            sos = butter(4, hi, "lowpass", fs=sr, output="sos")
        else:
            sos = butter(4, lo, "highpass", fs=sr, output="sos")
        out[name] = sosfiltfilt(sos, drums, axis=-1).astype(np.float32)
    return out


def _gpu_device():
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return torch.device("mps")
    return None


def _is_oom(exc: Exception) -> bool:
    return "out of memory" in str(exc).lower()


def separate(native: np.ndarray, sr: int) -> StemResult:
    """native: (samples, channels) float32. Never raises: failures come back as
    available=False with a plain note saying why."""
    try:
        import torch
        from demucs.apply import apply_model
        from demucs.audio import convert_audio
        from demucs.pretrained import get_model
    except ImportError as exc:
        return StemResult(
            available=False,
            notes=[
                f"Stem separation skipped: Demucs/PyTorch not installed ({exc.name}). "
                "Install with: pip install 'trackcard[stems]'"
            ],
        )

    notes: list[str] = []
    try:
        model = get_model(MODEL_NAME)
        model.eval()
    except Exception as exc:
        return StemResult(
            available=False,
            notes=[
                f"Stem separation skipped: could not load the {MODEL_NAME} model ({exc}). "
                "The first run downloads it (~80 MB); check your connection."
            ],
        )

    wav = torch.from_numpy(np.ascontiguousarray(native.T)).float()
    wav = convert_audio(wav, sr, model.samplerate, model.audio_channels)
    # Normalise like the demucs CLI does, then undo it on the output.
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav_n = (wav - mean) / std

    def run(device, shifts):
        model.to(device)
        with torch.no_grad():
            return apply_model(
                model, wav_n[None].to(device), split=True, overlap=0.25, shifts=shifts, progress=False
            )[0].cpu()

    gpu = _gpu_device()
    attempts = [(gpu, 2), (gpu, 0)] if gpu is not None else []
    attempts.append((torch.device("cpu"), 0))
    sources, used = None, None
    for i, (dev, shifts) in enumerate(attempts):
        try:
            sources = run(dev, shifts)
            used = (dev, shifts)
            break
        except RuntimeError as exc:
            if not _is_oom(exc) or i == len(attempts) - 1:
                return StemResult(available=False, notes=[f"Stem separation failed on {dev}: {exc}"])
            nxt = attempts[i + 1]
            notes.append(f"Demucs ran out of memory on {dev} (shifts={shifts}); retried on {nxt[0]} (shifts={nxt[1]}).")
            if dev.type == "cuda":
                torch.cuda.empty_cache()
    sources = sources * std + mean
    stems = {name: sources[model.sources.index(name)].numpy() for name in STEMS}
    stems.update(drum_bands(stems["drums"], model.samplerate))
    if gpu is None:
        notes.append("No GPU found; Demucs ran on CPU.")
    return StemResult(
        available=True,
        envelopes=compute_envelopes(stems, model.samplerate),
        device=str(used[0]),
        shifts=used[1],
        notes=notes,
    )


# ---- per-section activity -------------------------------------------------

ABS_FLOOR_DB = -50.0  # a stem frame quieter than this (dBFS RMS) counts as silent
REL_FLOOR_DB = -30.0  # ...or quieter than this relative to the stem's own loudest moments


@dataclass
class StemActivity:
    state: str  # "active" | "sparse" | "absent"
    density: float  # fraction of 100 ms frames where the stem is sounding
    level_db: float  # mean RMS while sounding, dBFS (-inf-ish if absent)
    rel_to_track_db: float  # this section's level vs the stem's loudest section


def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-9))


def stem_activity(env: dict, frame_hz: int, spans: list[tuple[float, float]]) -> list[StemActivity]:
    rms = np.asarray(env["rms"])
    if len(rms) == 0:
        return [StemActivity("absent", 0.0, -120.0, -120.0) for _ in spans]
    rdb = _db(rms)
    ref = np.percentile(rdb, 95)
    sounding = (rdb > ABS_FLOOR_DB) & (rdb > ref + REL_FLOOR_DB)
    raw = []
    for s, e in spans:
        a, b = int(s * frame_hz), max(int(e * frame_hz), int(s * frame_hz) + 1)
        m = sounding[a:b]
        dens = float(m.mean()) if len(m) else 0.0
        lvl = float(_db(np.sqrt(np.mean(rms[a:b][m] ** 2)))) if m.any() else -120.0
        raw.append((dens, lvl))
    loudest = max(l for _, l in raw)
    out = []
    for dens, lvl in raw:
        if dens < 0.10:
            state = "absent"
        elif dens < 0.45:
            state = "sparse"
        else:
            state = "active"
        out.append(StemActivity(state, round(dens, 2), round(lvl, 1), round(lvl - loudest, 1)))
    return out
