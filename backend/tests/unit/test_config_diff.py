from model_forensic.analysis.config_diff import ConfigDiffer, flatten
from model_forensic.domain.config import ChangeKind, ConfigurationSnapshot
from model_forensic.domain.run import RunRef

A, B = RunRef(backend="t", run_id="a"), RunRef(backend="t", run_id="b")


def snap(ref: RunRef, data: dict) -> list[ConfigurationSnapshot]:
    return [ConfigurationSnapshot(run=ref, source="config.yaml", data=data)]


def test_flatten_nested_and_list_of_dicts() -> None:
    f = flatten({"a": {"b": 1, "c": [1, 2]}, "heads": [{"type": "cls"}, {"type": "reg"}]})
    assert f == {"a.b": 1, "a.c": [1, 2], "heads[0].type": "cls", "heads[1].type": "reg"}


def test_semantic_equality_of_numeric_strings() -> None:
    d = ConfigDiffer().diff(A, snap(A, {"lr": "1e-4", "flag": "True"}), B, snap(B, {"lr": 0.0001, "flag": True}))
    assert d.changes == ()


def test_categories_noise_and_relative_change() -> None:
    d = ConfigDiffer().diff(
        A,
        snap(
            A,
            {
                "optimizer": {"lr": 2e-4},
                "temporal": {"num_frames": 4},
                "output_dir": "/a",
                "model": {"fusion": {"modalities": ["camera"]}},
            },
        ),
        B,
        snap(
            B,
            {
                "optimizer": {"lr": 4e-4},
                "temporal": {"num_frames": 8},
                "output_dir": "/b",
                "model": {"fusion": {"modalities": ["camera", "radar"]}},
                "new_key": 1,
            },
        ),
    )
    by = {c.path: c for c in d.changes}
    assert by["optimizer.lr"].category == "optimization" and abs(by["optimizer.lr"].relative_change - 1.0) < 1e-9
    assert by["temporal.num_frames"].category == "temporal"
    assert by["model.fusion.modalities"].category == "modality"
    assert by["output_dir"].is_noise and by["output_dir"] not in d.meaningful
    assert by["new_key"].kind == ChangeKind.ADDED


def test_seed_change_has_no_relative_change() -> None:
    d = ConfigDiffer().diff(A, snap(A, {"seed": 1}), B, snap(B, {"seed": 7}))
    assert d.changes[0].category == "randomness" and d.changes[0].relative_change is None
