from datetime import timedelta

from model_forensic.application.ports import SearchFilters
from model_forensic.domain.run import RunRef
from tests.conftest import NOW


def test_search_structured_and_fuzzy(container) -> None:
    svc = container.service
    assert svc.search("status:failed").total == 1
    assert {r.name for r in svc.search("tag:group=baseline-cl4f").runs} == {f"baseline-cl4f-s{i}" for i in range(3)}
    assert svc.search("radar").total >= 1  # fuzzy trigram match on name/description/tags
    assert svc.search("cfg:temporal.num_frames=8").runs[0].name == "cand-radar-8f"
    assert all(r.summary_metrics["NDS"] > 0.6 for r in svc.search("NDS>0.6").runs)
    assert {r.name for r in svc.search("parent:pipeline-nightly").runs} == {"nightly-train", "nightly-eval"}


def test_natural_language_search(container) -> None:
    res = container.service.search("find failed training jobs from last week", now=NOW)
    assert [r.name for r in res.runs] == ["lr-sweep-1e3"]
    assert res.notes and res.notes[0].startswith("interpreted as:")
    res = container.service.search("show experiments with uncertainty loss enabled")
    assert [r.name for r in res.runs] == ["cand-uncert"]


def test_date_filter_and_favorites(container) -> None:
    idx = container.service.index
    recent = idx.search(SearchFilters(created_after=NOW - timedelta(days=2)))
    assert {r.name for r in recent.runs} >= {"lr-sweep-1e3", "query-1800-spiky"}
    idx.set_favorite(RunRef(backend="local", run_id="cand-uncert"), True)
    assert [r.name for r in idx.search(SearchFilters(favorites_only=True)).runs] == ["cand-uncert"]
    container.service.sync()  # re-sync keeps favorites
    assert idx.search(SearchFilters(favorites_only=True)).total == 1


def test_suggest(container) -> None:
    assert "cand-radar-8f" in container.service.index.suggest("radar")
    assert "NDS" in container.service.index.suggest("N", field="metric")
