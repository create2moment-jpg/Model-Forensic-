"""Cross-attention mass / entropy per layer and modality, query ages, and the recorder hook."""

import math

import pytest

from model_forensic_instrument import MemorySink, attention_stats, query_age_stats

np = pytest.importorskip("numpy")


def test_mass_entropy_and_peak_per_modality() -> None:
    q, k = 4, 10
    w = np.zeros((2, q, k))
    w[..., :8] = 0.9 / 8  # camera tokens 0-7 get 90%
    w[..., 8:] = 0.1 / 2  # radar tokens 8-9 get 10%
    out = attention_stats(w, {"camera": slice(0, 8), "radar": [8, 9]}, layer=3)
    assert out["train/attn_mass[layer=3,modality=camera]"] == pytest.approx(0.9)
    assert out["train/attn_mass[layer=3,modality=radar]"] == pytest.approx(0.1)
    assert out["train/attn_entropy[layer=3,modality=camera]"] == pytest.approx(1.0)  # uniform inside
    expected = -(0.9 * math.log(0.9 / 8) + 0.1 * math.log(0.05)) / math.log(k)
    assert out["train/attn_entropy[layer=3]"] == pytest.approx(expected)
    assert out["train/attn_top1[layer=3]"] == pytest.approx(0.9 / 8)


def test_collapsed_attention_and_matched_queries_only() -> None:
    w = np.full((1, 3, 5), 0.2)
    w[0, 0] = [1, 0, 0, 0, 0]  # query 0 collapsed onto one token
    out = attention_stats(
        w, {"camera": np.array([True, True, True, False, False])}, layer=0, positive=[True, False, False]
    )
    assert out["train/attn_entropy[layer=0]"] == pytest.approx(0.0, abs=1e-6)
    assert out["train/attn_mass[layer=0,modality=camera]"] == pytest.approx(1.0)
    assert attention_stats(w, {"x": []}, layer=0, positive=[False, False, False]) == {}


def test_query_ages() -> None:
    out = query_age_stats([0, 0, 1, 2, 5, 9], positive=[True, False, True, False, True, False])
    assert out["train/q_age_mean[source=all]"] == pytest.approx(17 / 6)
    assert out["train/q_age_mean[source=matched]"] == pytest.approx(2.0)
    assert out["train/q_age_frac[age=0]"] == pytest.approx(1 / 3)
    assert out["train/q_age_frac[age=1-3]"] == pytest.approx(1 / 3)
    assert out["train/q_age_frac[age=4-inf]"] == pytest.approx(1 / 3)


def test_recorder_on_multihead_attention() -> None:
    torch = pytest.importorskip("torch")
    from model_forensic_instrument import Forensic

    class Dec(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layers = torch.nn.ModuleList([torch.nn.MultiheadAttention(8, 2, batch_first=True) for _ in range(2)])

        def forward(self, q, kv):  # type: ignore[no-untyped-def]
            for a in self.layers:
                q = q + a(q, kv, kv)[0]
            return q

    m = Dec()
    sink = MemorySink()
    fx = Forensic(m, sink=sink, attention={0: "layers.0", 1: "layers.1"}, every=1, attention_every=1)
    fx.start_step(0)
    fx.set_token_groups({"camera": slice(0, 6), "radar": slice(6, 10)})
    m(torch.randn(2, 5, 8), torch.randn(2, 10, 8))
    fx.after_step(0)
    names = {n for row in sink.rows for n in row["metrics"]} if hasattr(sink, "rows") else set(sink.metrics)
    masses = [n for n in names if n.startswith("train/attn_mass")]
    assert len(masses) == 4
