from datetime import datetime, timedelta, timezone

from model_forensic.infrastructure.search.query_parser import looks_natural, parse_query, rewrite_natural_language

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def test_dsl() -> None:
    f = parse_query(
        "status:failed,canceled exp:det tag:role=baseline tag:fav cfg:temporal.num_frames=8 NDS>0.6 AMOTA<0.5 since:7d foo",
        NOW,
    )
    assert f.statuses == ("failed", "canceled") and f.experiment == "det"
    assert f.tags == (("role", "baseline"), ("fav", None))
    assert f.params == (("temporal.num_frames", "8"),)
    assert f.metric_ranges == (("NDS", 0.6, None), ("AMOTA", None, 0.5))
    assert f.created_after == NOW - timedelta(days=7)
    assert f.text == "foo"


def test_natural_language_rewrites() -> None:
    dsl, notes = rewrite_natural_language("find failed sparse detector training jobs from last week", NOW)
    assert "status:failed" in dsl and "kind:training" in dsl and "since:7d" in dsl
    dsl, _ = rewrite_natural_language("show runs using 10-frame temporal windows", NOW)
    assert dsl == "cfg:temporal.num_frames=10"
    dsl, _ = rewrite_natural_language("show experiments with uncertainty loss enabled", NOW)
    assert "cfg:loss.uncertainty.enabled=true" in dsl
    _, notes = rewrite_natural_language("find runs where NDS improved but AMOTA dropped", NOW)
    assert any("requires a baseline" in n for n in notes)


def test_looks_natural() -> None:
    assert looks_natural("failed jobs from last week")
    assert not looks_natural("status:failed since:7d")
