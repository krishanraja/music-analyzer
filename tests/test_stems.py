import numpy as np
import pytest

from synth import SR, three_part_track
from trackcard.stems import FRAME_HZ, compute_envelopes, separate, stem_activity


def test_envelopes_shape_and_values():
    x = np.ones((2, SR * 3), dtype=np.float32) * 0.5
    env = compute_envelopes({"drums": x}, SR)
    assert len(env["drums"]["rms"]) == 3 * FRAME_HZ
    assert np.allclose(env["drums"]["rms"], 0.5) and np.allclose(env["drums"]["peak"], 0.5)


def test_envelopes_reject_bad_shape():
    with pytest.raises(ValueError):
        compute_envelopes({"x": np.zeros(10)}, SR)


def test_activity_states():
    # 10 s silent, 10 s on every other 100 ms frame, 10 s fully on
    rms = [0.0] * 100 + [0.3, 0.0] * 50 + [0.3] * 100
    acts = stem_activity({"rms": rms}, FRAME_HZ, [(0, 10), (10, 20), (20, 30)])
    assert [a.state for a in acts] == ["absent", "active", "active"]
    assert acts[0].density == 0.0 and acts[1].density == 0.5 and acts[2].density == 1.0
    rms2 = [0.0] * 100 + ([0.3] + [0.0] * 4) * 20 + [0.3] * 100
    acts2 = stem_activity({"rms": rms2}, FRAME_HZ, [(0, 10), (10, 20), (20, 30)])
    assert acts2[1].state == "sparse"


def test_separate_reports_missing_dependency(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("demucs"):
            raise ImportError("no demucs", name="demucs")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    r = separate(np.zeros((SR * 6, 2), dtype=np.float32), SR)
    assert not r.available and "pip install 'trackcard[stems]'" in r.notes[0]


@pytest.mark.slow
def test_real_demucs_on_synthetic_track():
    y, (b1, b2) = three_part_track()
    y = y[: int(b2 * SR) + SR * 4]  # A + B + a few seconds of A'
    r = separate(y, SR)
    assert r.available, r.notes
    acts = {s: stem_activity(r.envelopes[s], r.frame_hz, [(2, b1 - 2), (b1 + 2, b2 - 2)]) for s in r.envelopes}
    # drums appear only in the B section
    assert acts["drums"][0].state == "absent", acts["drums"]
    assert acts["drums"][1].state == "active", acts["drums"]
    assert acts["bass"][1].state == "active", acts["bass"]
