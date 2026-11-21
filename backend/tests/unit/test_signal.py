import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from model_forensic.analysis import signal


@given(st.lists(st.floats(-1e6, 1e6, allow_nan=False), min_size=1, max_size=200), st.floats(0.01, 1.0))
@settings(max_examples=100, deadline=None)
def test_ema_stays_within_range(values: list[float], alpha: float) -> None:
    a = np.asarray(values)
    out = signal.ema(a, alpha)
    assert out.shape == a.shape
    assert out.min() >= a.min() - 1e-6 * max(1, abs(a.min())) and out.max() <= a.max() + 1e-6 * max(1, abs(a.max()))


def test_detect_spikes_finds_injected_spikes_only() -> None:
    rng = np.random.default_rng(0)
    steps = np.arange(300, dtype=float)
    v = 1.0 + 0.01 * rng.standard_normal(300)
    v[[50, 200]] = 3.0
    spikes = signal.detect_spikes(steps, v, z=8)
    assert [s.index for s in spikes] == [50, 200]


def test_oscillation_index_monotone_vs_zigzag() -> None:
    mono = np.linspace(2, 1, 100)
    zig = 1 + 0.5 * (np.arange(100) % 2)
    assert signal.oscillation_index(mono) < 0.05
    assert signal.oscillation_index(zig) > 0.5


def test_relative_improvement_plateau() -> None:
    flat = np.r_[np.linspace(3, 1, 50), np.ones(50)]
    assert abs(signal.relative_improvement(flat, 0.3)) < 0.01
