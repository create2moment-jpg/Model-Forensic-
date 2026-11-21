"""Untrusted input from runs: YAML bombs, oversized documents, path traversal, CSP on generated HTML."""

import pytest

from model_forensic.shared import yamlsafe
from model_forensic.shared.errors import ValidationError
from model_forensic.shared.paths import safe_join

BOMB = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join(['*' + chr(97 + i)] * 9)}]\n" for i in range(8)
)


def test_yaml_alias_bomb_is_refused() -> None:
    with pytest.raises(yamlsafe.UnsafeYAMLError):
        yamlsafe.load(BOMB)


def test_yaml_size_limit_and_normal_aliases() -> None:
    with pytest.raises(yamlsafe.UnsafeYAMLError):
        yamlsafe.load("a: 1\n" * 10, max_bytes=20)
    assert yamlsafe.load("base: &b {lr: 1}\nrun: *b\n") == {"base": {"lr": 1}, "run": {"lr": 1}}
    assert yamlsafe.load("p: !!python/object/apply:pathlib.PosixPath [/, opt, x.yml]\n") == {"p": "/opt/x.yml"}


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs", "a/../../b", "C:\\x", "a\x00b", ""])
def test_safe_join_rejects_traversal(tmp_path, bad: str) -> None:
    with pytest.raises(ValidationError):
        safe_join(tmp_path, bad)


def test_local_connector_skips_bomb_config(tmp_path) -> None:
    import json

    from model_forensic.infrastructure.connectors.local.connector import LocalRunSource

    d = tmp_path / "r1"
    (d / "configs").mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"name": "r1", "status": "completed"}))
    (d / "configs" / "evil.yaml").write_text(BOMB)
    (d / "configs" / "ok.yaml").write_text("lr: 0.1\n")
    snaps = LocalRunSource("local", tmp_path).get_configs("r1")
    assert [s.data for s in snaps if "ok" in s.source] == [{"lr": 0.1}]
    assert not any("evil" in s.source for s in snaps)


def test_api_rejects_traversal_run_id_and_bomb(container) -> None:
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    c = TestClient(create_app(container), raise_server_exceptions=False)
    r = c.get("/api/v1/runs/local:..%2F..%2Fetc")
    assert r.status_code in (400, 404) and "Traceback" not in r.text
    r = c.post("/api/v1/gate", json={"config": BOMB})
    assert r.status_code == 400


def test_study_book_has_csp(container) -> None:
    st = container.studies.create("t", goal="g", runs=["local:xf-pipe-finetune"], baseline="local:xf-online-baseline")
    html_path, _ = container.study_book(st.id)
    html = html_path.read_text()
    assert "Content-Security-Policy" in html and "<script src=" not in html


def test_security_headers(container) -> None:
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    r = TestClient(create_app(container)).get("/api/v1/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Referrer-Policy"] == "no-referrer"
