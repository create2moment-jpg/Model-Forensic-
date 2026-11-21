"""docs/prompt-catalog.md must match the prompt packs (run scripts/gen_prompt_catalog.py after editing packs)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_catalog_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "gen_prompt_catalog.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "docs" / "prompt-catalog.md").read_text() == mod.render(), (
        "run: python scripts/gen_prompt_catalog.py"
    )
