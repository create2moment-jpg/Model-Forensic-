"""Knowledge sources (many codebases, one inbox), the documentation index, and how they reach the harness."""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from model_forensic.knowledge import KnowledgeStore
from model_forensic.knowledge.docindex import DocIndex, fts_query, sections
from model_forensic.knowledge.sources import KnowledgeSource, discover, safe_join, walk

ENTRY = """entries:
  - id: {id}
    kind: fact
    statement: {stmt}
    status: active
"""


def test_discover_inbox_config_and_env(tmp_path: Path) -> None:
    env = {
        "MF_KNOWLEDGE_SOURCE_PERCEPTION": str(tmp_path / "perc"),
        "MF_KNOWLEDGE_SOURCE_DATA__TOOLS": str(tmp_path / "dt"),
        "MF_KNOWLEDGE_SOURCE_EMPTY": " ",
        "DOCS_ROOT": str(tmp_path),
    }
    conf = [KnowledgeSource(name="Team", path="${DOCS_ROOT}/team", trust="as_written")]
    got = discover(conf, tmp_path / "inbox", env)
    assert [s.name for s in got] == ["inbox", "team", "data-tools", "perception"]
    assert got[0].inbox and got[0].resolved() == tmp_path / "inbox"
    assert got[3].env == "MF_KNOWLEDGE_SOURCE_PERCEPTION" and got[3].codebase == "perception"
    # an env source with a configured name does not replace the configured one
    assert discover(conf, tmp_path, {"MF_KNOWLEDGE_SOURCE_TEAM": "/x"})[1].trust == "as_written"
    # the inbox can be moved by MF_KNOWLEDGE_INBOX
    assert discover((), tmp_path, {"MF_KNOWLEDGE_INBOX": str(tmp_path / "elsewhere")})[0].path.endswith("elsewhere")
    assert KnowledgeSource(name="x", path="${NOT_SET_ANYWHERE_123}/a").resolved() is None


def test_walk_prunes_and_caps(tmp_path: Path) -> None:
    for rel in ("a.md", "sub/b.md", ".git/c.md", "node_modules/d.md", "draft/e.md", "big.md", "f.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * (2048 if rel == "big.md" else 10))
    src = KnowledgeSource(name="s", path=str(tmp_path), exclude=["draft/*"], max_file_kb=1)
    assert sorted(f.rel for f in walk(src, (".md",))) == ["a.md", "sub/b.md"]
    assert len(list(walk(src.model_copy(update={"max_files": 1}), (".md",)))) == 1
    assert safe_join(tmp_path, "../etc/passwd") is None and safe_join(tmp_path, "sub/b.md") is not None


def test_store_reads_every_source_with_trust_and_overrides(tmp_path: Path) -> None:
    inbox, perc, team = tmp_path / "inbox", tmp_path / "perc", tmp_path / "team"
    for d in (inbox, perc / "notes", team):
        d.mkdir(parents=True)
    (perc / "notes" / "k.yaml").write_text(ENTRY.format(id="K-perc", stmt="Radar head is auxiliary"))
    (perc / "ci.yaml").write_text("jobs: {build: {steps: []}}\n")  # not knowledge: silently ignored
    (team / "k.yaml").write_text(ENTRY.format(id="K-team", stmt="Animal is set aside"))
    (inbox / "uncertainty-2026-11-22.yaml").write_text(
        ENTRY.format(id="K-agent", stmt="Sigma is log sigma").replace(
            "status: active", "status: active\n    source: {via: harness:uncertainty_weighting}"
        )
    )
    sources = [
        KnowledgeSource(name="inbox", path=str(inbox), inbox=True, trust="as_written"),
        KnowledgeSource(name="perception", path=str(perc)),
        KnowledgeSource(name="team", path=str(team), trust="as_written"),
    ]
    st = KnowledgeStore(inbox, sources)
    entries, notes = st.load()
    by = {e.id: e for e in entries}
    assert notes == []
    assert by["K-perc"].status == "proposed"  # an agent-written codebase source is reviewed first
    assert by["K-team"].status == "active"  # curated source: as written
    assert by["K-agent"].status == "proposed"  # harness output in the inbox is reviewed first
    assert st.origins["K-perc"] == "perception:notes/k.yaml"
    # accepting writes an override into the inbox main file, which wins
    st.upsert(by["K-perc"].model_copy(update={"status": "active"}))
    assert {e.id: e.status for e in st.load()[0]}["K-perc"] == "active"
    assert st.origins["K-perc"] == "inbox:knowledge.local.yaml"
    # rejecting an entry of a read-only source retires it (the file is not touched)
    assert st.delete("K-team")
    assert {e.id: e.status for e in st.load()[0]}["K-team"] == "retired"
    assert "K-team" in (team / "k.yaml").read_text()
    assert not st.delete("K-nowhere")


def test_store_caches_by_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(ENTRY.format(id="K-a", stmt="first version"))
    st = KnowledgeStore(tmp_path, [KnowledgeSource(name="inbox", path=str(tmp_path), inbox=True)])
    assert st.load()[0][0].statement == "first version"
    cached = dict(st._cache)
    st.load()
    assert st._cache == cached
    (tmp_path / "a.yaml").write_text(ENTRY.format(id="K-a", stmt="second version, longer"))
    os.utime(tmp_path / "a.yaml", (time.time() + 5, time.time() + 5))
    assert st.load()[0][0].statement == "second version, longer"


def test_markdown_sections_respect_code_fences() -> None:
    md = "# Title\nintro\n## Loss\nsigma\n```\n# not a heading\n```\n## Empty\n## Metrics\nap\n"
    got = [(s.heading, s.anchor) for s in sections(md, "x.md")]
    assert got == [("Title", "title"), ("Title › Loss", "loss"), ("Title › Metrics", "metrics")]
    assert "# not a heading" in sections(md, "x.md")[1].body
    assert len(sections("plain text", "notes.txt")) == 1
    assert fts_query('sigma "drop table') == '"sigma" "drop" "table"*' and fts_query("  ;; ") == ""


def test_doc_index_incremental_search_and_read(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "losses").mkdir(parents=True)
    (root / "losses" / "uncertainty.md").write_text(
        "# Uncertainty weighting\n## How sigma is learned\nThe task_log_sigma parameter is learned per term.\n"
    )
    (root / "setup.md").write_text("# Sensor setups\n## 1v5r\nOne front camera and five radars.\n")
    src = [KnowledgeSource(name="perception", path=str(root), codebase="perception")]
    idx = DocIndex(tmp_path / "docs.sqlite")
    r = idx.refresh(src)[0]
    assert (r.files, r.changed, r.removed, r.error) == (2, 2, 0, "")
    assert idx.refresh(src)[0].changed == 0  # unchanged files are not re-read
    hits = idx.search("sigma learned")
    assert hits and hits[0].path == "losses/uncertainty.md" and hits[0].heading.endswith("How sigma is learned")
    assert hits[0].anchor == "how-sigma-is-learned" and hits[0].codebase == "perception"
    assert idx.search("radar")[0].heading == "Sensor setups › 1v5r"
    assert idx.search("radar banana")[0].heading == "Sensor setups › 1v5r"  # no section has both → any word
    assert fts_query("a b", any_word=True) == '"a" OR "b"*'
    assert isinstance(idx.search('") OR 1=1 -- NEAR(*'), list)  # hostile query text never raises
    title, text = idx.read(src, "perception", "setup.md")
    assert title == "Sensor setups" and "five radars" in text
    for bad in ("../docs.sqlite", "nope.md"):
        try:
            idx.read(src, "perception", bad)
            raise AssertionError("should not read " + bad)
        except FileNotFoundError:
            pass
    (root / "setup.md").unlink()
    assert idx.refresh(src)[0].removed == 1 and idx.search("radar") == []
    assert idx.refresh([]) == [] and idx.counts() == {}  # a removed source drops its documents


def test_doc_index_scales(tmp_path: Path) -> None:
    root = tmp_path / "many"
    for i in range(1500):
        d = root / f"area{i % 30}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"note{i}.md").write_text(f"# Note {i}\n## Detail\nterm{i} relates to loss family {i % 7}.\n")
    src = [KnowledgeSource(name="big", path=str(root))]
    idx = DocIndex(tmp_path / "docs.sqlite")
    t0 = time.monotonic()
    assert idx.refresh(src)[0].files == 1500
    first = time.monotonic() - t0
    t1 = time.monotonic()
    assert idx.refresh(src)[0].changed == 0
    second = time.monotonic() - t1
    t2 = time.monotonic()
    assert idx.search("term1234")[0].path == "area4/note1234.md"
    assert time.monotonic() - t2 < 0.5
    assert first < 30 and second < first and second < 5


def test_api_sources_search_and_brief(container) -> None:
    from model_forensic.api.app import create_app

    inbox = container.settings.knowledge_dir
    (inbox / "docs" / "perception").mkdir(parents=True)
    (inbox / "docs" / "perception" / "uncertainty.md").write_text(
        "# Uncertainty weighting\n## Sigma\nEach loss term has a learned log sigma.\n"
    )
    container.knowledge = KnowledgeStore(inbox, discover((), inbox, {}))
    c = TestClient(create_app(container))
    s = c.get("/api/v1/knowledge/sources").json()
    assert s["sources"][0]["name"] == "inbox" and s["sources"][0]["docs"] == 1 and s["sources"][0]["exists"]
    hits = c.get("/api/v1/docs/search", params={"q": "log sigma"}).json()["hits"]
    assert hits[0]["path"] == "docs/perception/uncertainty.md"
    f = c.get("/api/v1/docs/file", params={"source": "inbox", "path": hits[0]["path"]}).json()
    assert f["title"] == "Uncertainty weighting"
    assert c.get("/api/v1/docs/file", params={"source": "inbox", "path": "../../etc/passwd"}).status_code == 404
    assert c.post("/api/v1/knowledge/sources/refresh").json()["reports"][0]["files"] == 1
    brief = c.get("/api/v1/runs/local:xf-online-baseline/harness-brief", params={"task": "uncertainty_weighting"}).text
    assert "## Where to write" in brief and str(inbox) in brief
    assert "inbox:docs/perception/uncertainty.md#sigma" in brief
