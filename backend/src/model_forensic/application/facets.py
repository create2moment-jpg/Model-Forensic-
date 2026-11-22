"""Filter facets: how metric dimensions map onto the filters people use (class, modality, distance, …).

Defaults ship in ``defaults/facets.yaml``; a project overrides them in ``config/facets.local.yaml`` (git-ignored) or
``MF_FACETS_CONFIG``. The UI applies the same rules (``web/src/pages/compare/Filters.tsx::facetsOf``); this module
serves the merged configuration, lists what a run's names actually carry, and writes the brief a local LLM answers
with a ``facets.local.yaml`` proposal. A broken file yields the defaults plus a notice — it never stops the app.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

DEFAULTS = Path(__file__).resolve().parents[1] / "defaults" / "facets.yaml"
LOCAL = Path("config/facets.local.yaml")


class FacetSpec(BaseModel):
    aliases: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    value_aliases: dict[str, str] = Field(default_factory=dict)
    from_name: bool = False
    only_listed: bool = False


class FacetConfig(BaseModel):
    order: list[str] = Field(default_factory=list)
    hide_dims: list[str] = Field(default_factory=list)
    facets: dict[str, FacetSpec] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    notice: str | None = None


def local_path() -> Path:
    from model_forensic.shared.paths import home_path

    return home_path(os.environ.get("MF_FACETS_CONFIG", str(LOCAL)))


def _read(p: Path) -> dict[str, Any]:
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("the file must be a mapping (order / hide_dims / facets)")
    FacetConfig.model_validate({k: v for k, v in data.items() if k in ("order", "hide_dims", "facets")})
    return data


def validate(p: Path) -> list[str]:
    """Problems with a facets file (empty when it is usable)."""
    try:
        _read(p)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as e:
        return [str(e)]
    return []


def load(defaults: Path = DEFAULTS, local: Path | None = None) -> FacetConfig:
    base = _read(defaults)
    sources = [str(defaults)]
    notice = None
    lp = local or local_path()
    if lp.is_file():
        try:
            over = _read(lp)
            base["facets"] = {**(base.get("facets") or {}), **(over.get("facets") or {})}
            for k in ("order", "hide_dims"):
                if k in over:
                    base[k] = over[k]
            sources.append(str(lp))
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as e:
            notice = f"{lp}: not used ({str(e).splitlines()[0]}) — the defaults apply"
    cfg = FacetConfig.model_validate({k: v for k, v in base.items() if k in ("order", "hide_dims", "facets")})
    cfg.sources, cfg.notice = sources, notice
    return cfg


def parse(name: str) -> tuple[str, dict[str, str]]:
    """``val/AP[class=car,dist=40]`` → ``("val/AP", {"class": "car", "dist": "40"})``."""
    i = name.find("[")
    if i < 0 or not name.endswith("]"):
        return name, {}
    dims = {}
    for part in name[i + 1 : -1].split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            dims[k.strip()] = v.strip()
    return name[:i], dims


def facets_of(name: str, cfg: FacetConfig) -> dict[str, str]:
    """The facets a name carries: dimensions renamed to their facet, value aliases applied, modality from the name."""
    family, dims = parse(name)
    alias = {a.lower(): f for f, spec in cfg.facets.items() for a in [f, *spec.aliases]}
    out: dict[str, str] = {}
    for d, v in dims.items():
        if d in cfg.hide_dims:
            continue
        f = alias.get(d.lower(), d)
        spec = cfg.facets.get(f)
        out[f] = spec.value_aliases.get(v, v) if spec else v
    hay = f"{' '.join(dims.values())} {family}".lower()
    for f, spec in cfg.facets.items():
        if f in out or not spec.from_name:
            continue
        for v in [*spec.values, *spec.value_aliases]:
            if re.search(rf"(^|[^a-z]){re.escape(v.lower())}", hay):
                out[f] = spec.value_aliases.get(v, v)
                break
    return out


def observed(names: list[str], cfg: FacetConfig) -> dict[str, Any]:
    """What the names carry: per facet its values (with counts), and the raw dimensions no facet claims."""
    facets: dict[str, dict[str, int]] = {}
    raw: dict[str, set[str]] = {}
    for n in names:
        for f, v in facets_of(n, cfg).items():
            facets.setdefault(f, {})[v] = facets.setdefault(f, {}).get(v, 0) + 1
        for d, v in parse(n)[1].items():
            raw.setdefault(d, set()).add(v)
    claimed = {a.lower() for f, s in cfg.facets.items() for a in [f, *s.aliases]}
    return {
        "facets": {
            f: dict(sorted(facets[f].items()))
            for f in sorted(facets, key=lambda f: (cfg.order.index(f) if f in cfg.order else len(cfg.order), f))
        },
        "unclaimed_dims": {d: sorted(v) for d, v in sorted(raw.items()) if d.lower() not in claimed},
    }


def brief(names: list[str], cfg: FacetConfig, prompt: str = "") -> str:
    """A prompt for a local LLM: the names a run logged, the current facets, what to write and where."""
    obs = observed(names, cfg)
    lines = [
        "# Model Forensic brief — filter facets",
        "",
        "Propose `config/facets.local.yaml` so the metric filters (class, modality, distance, …) offer the right",
        "values for this project. Use only names and values that appear below; never invent them.",
        "",
        "## Current configuration",
        "",
        "```yaml",
        yaml.safe_dump(cfg.model_dump(exclude={"sources", "notice"}), sort_keys=False).rstrip(),
        "```",
        "",
        f"Read from: {', '.join(cfg.sources)}" + (f"  \n**Notice:** {cfg.notice}" if cfg.notice else ""),
        "",
        "## What the run's names carry with this configuration",
        "",
    ]
    for f, vals in obs["facets"].items():
        lines.append(f"- **{f}**: " + ", ".join(f"`{v}` ({n})" for v, n in vals.items()))
    if obs["unclaimed_dims"]:
        lines += ["", "## Dimensions no facet claims (map them with `aliases`, hide them, or leave them)", ""]
        lines += [f"- `{d}`: " + ", ".join(f"`{v}`" for v in vs[:40]) for d, vs in obs["unclaimed_dims"].items()]
    lines += ["", f"## Metric and loss names as logged ({len(names)})", ""]
    lines += [f"- `{n}`" for n in sorted(names)[:600]]
    if len(names) > 600:
        lines.append(f"- … {len(names) - 600} more")
    lines += [
        "",
        "## Where to write",
        "",
        f"- `{local_path()}` (git-ignored; only the facets you change — the others keep their defaults)",
        "- Validate: `mf facets --check " + str(local_path()) + "`, then `mf facets --run <id>` to see the result.",
    ]
    if prompt:
        lines += ["", "---", "", prompt.strip()]
    return "\n".join(lines).rstrip() + "\n"
