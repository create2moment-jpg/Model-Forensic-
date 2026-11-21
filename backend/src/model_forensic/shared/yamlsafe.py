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


MAX_BYTES = 20_000_000  # composed configs are ~100 kB; anything this large is not a config
MAX_NODES = 2_000_000  # nodes after alias expansion; a "billion laughs" document expands to hundreds of millions


class UnsafeYAMLError(ValueError):
    pass


def _expanded_nodes(node: yaml.Node, memo: dict[int, int]) -> int:
    """Node count with every alias expanded — computed on the composed graph (aliases are shared nodes), so it
    costs O(document), not O(expansion)."""
    key = id(node)
    if key in memo:
        return memo[key]
    memo[key] = 1  # cycle guard (recursive anchors)
    if isinstance(node, yaml.SequenceNode):
        n = 1 + sum(_expanded_nodes(c, memo) for c in node.value)
    elif isinstance(node, yaml.MappingNode):
        n = 1 + sum(_expanded_nodes(k, memo) + _expanded_nodes(v, memo) for k, v in node.value)
    else:
        n = 1
    memo[key] = n
    return n


def load(text: str, max_bytes: int = MAX_BYTES, max_nodes: int = MAX_NODES) -> Any:
    """Like ``yaml.safe_load``, but Python/custom tags yield plain data instead of an error.

    Untrusted input limits: size, and the node count after alias expansion (anchors referencing anchors expand
    exponentially when the config is later flattened or serialised)."""
    if len(text) > max_bytes:
        raise UnsafeYAMLError(f"YAML document too large ({len(text):,} characters > {max_bytes:,})")
    if "*" in text:
        root = yaml.compose(text, Loader=_TolerantLoader)
        size = _expanded_nodes(root, {}) if root is not None else 0
        if size > max_nodes:
            raise UnsafeYAMLError(f"YAML aliases expand to {size:,} nodes (> {max_nodes:,}); refusing")
    return yaml.load(text, Loader=_TolerantLoader)  # noqa: S506 - SafeLoader subclass, constructs no objects
