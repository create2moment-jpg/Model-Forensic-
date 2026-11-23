"""Matching instability (A3), logit growth (A4) and per-layer groups (A5) of the kit."""

import math

import numpy as np
import pytest

from model_forensic_instrument import MatchTracker, attn_logit_stats, layer_groups, output_logit_stats


def test_matching_instability_between_measurements_and_layers() -> None:
    mt = MatchTracker()
    mt.record(0, [0, 1, 2, 3], [0, 1, 2, 3])
    mt.record(1, [0, 1, 2, 3], [0, 1, 2, 3])
    first = mt.flush()
    assert "val/match_instability[layer=0]" not in first  # nothing to compare with yet
    assert first["val/match_layer_change[layer=1]"] == 0.0
    mt.record(0, [0, 1, 2, 3], [1, 0, 2, 3])  # objects 0 and 1 swapped queries
    mt.record(1, np.array([5, 1, 2, 3]), np.array([0, 1, 2, 3]))  # object 0 → query 5
    out = mt.flush()
    assert out["val/match_instability[layer=0]"] == pytest.approx(0.5)
    assert out["val/match_instability[layer=1]"] == pytest.approx(0.25)
    assert out["val/match_count[layer=0]"] == 4.0
    # layer 1 vs layer 0 this time: object 0 (query 5 vs 1), object 1 (1 vs 0) → 2 of 4
    assert out["val/match_layer_change[layer=1]"] == pytest.approx(0.5)


def test_assignment_form_samples_and_errors() -> None:
    mt = MatchTracker()
    mt.record_assignment(0, [-1, 0, -1, 1], sample="a")
    mt.record_assignment(0, [0, -1], sample="b")
    mt.flush()
    mt.record_assignment(0, [-1, 0, 1, -1], sample="a")  # object 1 moved from query 3 to query 2
    mt.record_assignment(0, [0, -1], sample="b")
    assert mt.flush()["val/match_instability[layer=0]"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        mt.record(0, [0, 1], [0])


def test_attention_logits_and_norms() -> None:
    rng = np.random.default_rng(0)
    q, k = rng.normal(size=(2, 8, 16)), rng.normal(size=(2, 30, 16))
    small = attn_logit_stats(q, k, layer=3)
    big = attn_logit_stats(q * 10, k * 10, layer=3)
    assert set(small) == {"train/attn_logit_max[layer=3]", "train/q_norm[layer=3]", "train/k_norm[layer=3]"}
    assert big["train/attn_logit_max[layer=3]"] == pytest.approx(100 * small["train/attn_logit_max[layer=3]"])
    expect = float(np.abs(np.einsum("bqd,bkd->bqk", q, k) / math.sqrt(16)).max())
    assert small["train/attn_logit_max[layer=3]"] == pytest.approx(expect)
    with pytest.raises(ValueError):
        attn_logit_stats(q, rng.normal(size=(2, 30, 8)), layer=0)


def test_output_logits() -> None:
    out = output_logit_stats(np.array([[-3.0, 1.0], [np.nan, 2.0]]), layer=5)
    assert out == {"train/logit_max[layer=5]": 3.0, "train/logit_rms[layer=5]": pytest.approx(math.sqrt(14 / 3)),
                   "train/logit_mean[layer=5]": pytest.approx(0.0)}  # fmt: skip
    assert output_logit_stats(np.array([np.nan])) == {}
    assert "train/logit_max" in output_logit_stats(np.ones(3))


def test_layer_groups() -> None:
    assert layer_groups("head.decoder.layers", 3) == {
        "dec_layer0": "head.decoder.layers.0", "dec_layer1": "head.decoder.layers.1", "dec_layer2": "head.decoder.layers.2",
    }  # fmt: skip
