"""YAML for untrusted run configs: safe, but tolerant of the Python tags Hydra/OmegaConf dumps contain.

Composed configs written by training pipelines often contain ``!!python/object/apply:pathlib.PosixPath`` (a list
of path parts) or other ``!!python/...`` tags. ``yaml.safe_load`` rejects the whole file; this loader never
constructs objects — tagged nodes become plain data, and PosixPath part lists are joined back into a path string.
"""

from __future__ import annotations

from typing import Any

import yaml


class _TolerantLoader(yaml.SafeLoader):
    pass


def _join_path(parts: list[Any]) -> str:
    pieces = [str(p) for p in parts]
    if pieces and pieces[0] == "/":
        return "/" + "/".join(pieces[1:])
    return "/".join(pieces)


def _any_tag(loader: _TolerantLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        seq = loader.construct_sequence(node, deep=True)
        if "Path" in suffix:
            return _join_path(seq)
        return seq
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None


_TolerantLoader.add_multi_constructor("tag:yaml.org,2002:python/", _any_tag)
_TolerantLoader.add_multi_constructor("!", _any_tag)


def load(text: str) -> Any:
    """Like ``yaml.safe_load``, but Python/custom tags yield plain data instead of an error."""
    return yaml.load(text, Loader=_TolerantLoader)  # noqa: S506 - SafeLoader subclass, constructs no objects
