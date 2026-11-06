import numpy as np
import pytest
import soundfile as sf

from synth import SR, click_track, progression, three_part_track
from trackcard.audio import TrackcardError, load_audio
from trackcard.key import analyze_key, compatible_camelot, key_from_chroma
from trackcard.loudness import analyze_loudness
from trackcard.spectrum import band_profile
from trackcard.tempo import analyze_tempo


def _resample(y):
    import librosa

    return librosa.resample(y.astype(np.float32), orig_sr=SR, target_sr=22050), 22050


@pytest.mark.parametrize("bpm", [124.0, 125.0, 128.0, 140.0, 100.0, 123.5])
def test_click_track_bpm_exact_on_fixed_grid(bpm):
    y, sr = _resample(click_track(bpm, 30))
    r = analyze_tempo(y, sr)
    assert r.grid == "fixed"
    assert abs(r.bpm - bpm) <= 0.05, (r.bpm, r.raw_bpm)


def test_tempo_change_is_not_forced_onto_one_grid():
    y = np.concatenate([click_track(120.0, 30), click_track(128.0, 30)])
    r = analyze_tempo(*_resample(y))
    assert r.grid == "tracked"
    assert "no single steady grid" in r.confidence_note


def test_bpm_override_uses_given_tempo():
    y, sr = _resample(click_track(125.0, 30))
    r = analyze_tempo(y, sr, bpm_override=125.0)
    assert r.grid == "producer" and r.bpm == 125.0
    assert abs(np.median(np.diff(r.beats)) - 0.48) < 1e-6
    assert r.downbeats[0] < 0.05


def test_downbeat_phase_on_accented_click():
    # Start with three unaccented-feeling pickup beats so beat 1 is not at t=0.
    bpm = 120.0
    y = click_track(bpm, 30)
    pre = int(SR * 60 / bpm)  # shift by one beat: the first audible beat is beat 4
    y = np.concatenate([np.zeros(pre), y])[: len(y)]
    yr, sr = _resample(y)
    r = analyze_tempo(yr, sr)
    # Downbeats should land on the accented kicks, which are at 0.5 s + n*2 s.
    first = r.downbeats[0] if r.downbeats[0] > 0.3 else r.downbeats[1]
    assert abs(((first - 0.5) % 2.0)) < 0.08 or abs(((first - 0.5) % 2.0) - 2.0) < 0.08


def test_octave_correction_triggers_below_range():
    from trackcard.tempo import correct_octave
    import librosa

    y, sr = _resample(click_track(150.0, 30))
    env = librosa.onset.onset_strength(y=y, sr=sr)
    _, frames = librosa.beat.beat_track(y=y, sr=sr, onset_envelope=env, start_bpm=75, trim=False)
    bpm, _, corrected = correct_octave(y, sr, 75.0, frames, env)
    assert corrected
    assert PREFERRED_OK(bpm)


def PREFERRED_OK(bpm):
    return 85 <= bpm <= 170


@pytest.mark.parametrize(
    "chords,expected,camelot",
    [
        ([("A", True), ("D", True), ("E", False), ("A", True)], "A minor", "8A"),
        ([("C", False), ("F", False), ("G", False), ("C", False)], "C major", "8B"),
        ([("E", False), ("A", False), ("B", False), ("E", False)], "E major", "12B"),
        ([("F#", True), ("B", True), ("C#", False), ("F#", True)], "F# minor", "11A"),
    ],
)
def test_chord_progression_key(chords, expected, camelot):
    y, sr = _resample(progression(chords))
    k = analyze_key(y, sr)
    assert k.name == expected, (k.name, k.runner_up, k.margin)
    assert k.camelot == camelot


def test_key_profile_direct():
    from trackcard.key import MAJOR

    k = key_from_chroma(np.roll(MAJOR, 7))  # G major profile
    assert k.name == "G major" and k.camelot == "9B"


def test_compatible_camelot():
    assert compatible_camelot("8A") == ["8A", "9A", "7A", "8B"]
    assert compatible_camelot("12B") == ["12B", "1B", "11B", "12A"]
    assert compatible_camelot("1A") == ["1A", "2A", "12A", "1B"]


def test_spectrum_flags_no_air_and_no_sub():
    t = np.arange(SR * 4) / SR
    y = np.sin(2 * np.pi * 440 * t) + 0.5 * np.sin(2 * np.pi * 1200 * t)
    p = band_profile(y, SR, 0, 4, "drop")
    assert any("nothing above 2 kHz in the drop" in f for f in p.flags), p.flags
    assert any("no sub" in f for f in p.flags)


def test_spectrum_full_band_noise_has_air():
    y = np.random.default_rng(0).standard_normal(SR * 4) * 0.1
    p = band_profile(y, SR, 0, 4, "drop")
    assert not any("above 8 kHz" in f for f in p.flags), p.flags
    assert p.rel_db["air"] == 0.0  # white noise: widest band is loudest


def test_loudness_basic():
    t = np.arange(SR * 6) / SR
    y = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    r = analyze_loudness(np.stack([y, y], 1), SR)
    assert abs(r.peak_dbfs - (-6.0)) < 0.1
    assert abs(r.rms_dbfs - (-9.0)) < 0.1
    assert abs(r.crest_db - 3.0) < 0.2
    assert len(r.per_second_dbfs) == 6


def test_load_audio_errors(tmp_path):
    with pytest.raises(TrackcardError, match="File not found"):
        load_audio(tmp_path / "nope.wav")
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(TrackcardError, match="unsupported file type"):
        load_audio(txt)
    short = tmp_path / "short.wav"
    sf.write(short, np.zeros(SR), SR)
    with pytest.raises(TrackcardError, match="at least"):
        load_audio(short)
    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(SR * 6), SR)
    with pytest.raises(TrackcardError, match="silent"):
        load_audio(silent)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF not really")
    with pytest.raises(TrackcardError, match="Could not read"):
        load_audio(bad)


def test_load_audio_ok(tmp_path):
    y, _ = three_part_track()
    p = tmp_path / "t.wav"
    sf.write(p, y, SR)
    a = load_audio(p)
    assert a.native_sr == SR and a.channels == 2 and a.sr == 22050
    assert abs(a.duration - len(y) / SR) < 0.05


def test_key_alternative_for_neighbour_keys():
    # Pitch content of a D-minor riff that leans on G and A (the Closure case):
    # strong A, G, Bb, D; weak F, E, C.
    from trackcard.key import PITCHES

    w = {"A": 0.42, "G": 0.41, "A#": 0.31, "D": 0.28, "G#": 0.25, "B": 0.17, "F#": 0.15,
         "F": 0.12, "E": 0.11, "C": 0.10, "C#": 0.08, "D#": 0.04}
    k = key_from_chroma(np.array([w[p] for p in PITCHES]))
    assert {k.name, k.alternative} >= {"G minor", "D minor"}, (k.name, k.alternative)
    assert k.alternative_camelot in {"6A", "7A"} and k.alternative_reason


def test_no_alternative_for_unambiguous_key():
    from trackcard.key import MAJOR

    k = key_from_chroma(MAJOR ** 3)  # C major, exaggerated
    assert k.name == "C major" and k.alternative is None


@pytest.mark.parametrize("text,expected", [
    ("D minor", (2, False)), ("Dm", (2, False)), ("d min", (2, False)), ("7A", (2, False)),
    ("Bb", (10, True)), ("Bbm", (10, False)), ("F# major", (6, True)), ("8B", (0, True)), ("E#m", (5, False)),
])
def test_parse_key(text, expected):
    from trackcard.key import parse_key

    assert parse_key(text) == expected


@pytest.mark.parametrize("bad", ["H minor", "13A", "Dorian", ""])
def test_parse_key_rejects(bad):
    from trackcard.key import parse_key

    with pytest.raises(ValueError, match="Use forms like"):
        parse_key(bad)
