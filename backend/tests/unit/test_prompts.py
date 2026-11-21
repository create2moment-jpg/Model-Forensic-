import pytest

from model_forensic.ai.prompts import PromptLibrary, PromptTemplate
from model_forensic.shared.errors import ValidationError


def test_builtin_pack_loads_and_renders() -> None:
    lib = PromptLibrary.load()
    t = lib.get("why_worse")
    text = t.render({"run_id": "a", "baseline_run_id": "b"})
    assert "candidate a" in text and "baseline b" in text
    with pytest.raises(ValidationError):
        t.render({"run_id": "a"})


def test_optional_sections() -> None:
    t = PromptLibrary.load().get("loss_decomposition")
    assert "focusing on" not in t.render({"run_id": "a"})
    assert "focusing on loss/cls" in t.render({"run_id": "a", "loss_terms": "loss/cls"})


def test_versioning_never_overwrites() -> None:
    lib = PromptLibrary.load()
    v2 = lib.new_version("explain_run", "Explain {{run_id}} briefly.")
    assert v2.version == 2 and lib.get("explain_run").version == 2 and lib.get("explain_run", 1).version == 1
    with pytest.raises(ValidationError):
        lib.add(PromptTemplate(id="explain_run", version=2, title="x", template="{{run_id}}"))


def test_unknown_variable_rejected() -> None:
    with pytest.raises(ValidationError):
        PromptLibrary().add(PromptTemplate(id="x", version=1, title="x", template="{{api_key}}"))
