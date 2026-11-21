"""Loss-term gradient probe: which loss terms actually steer a shared module, and do they conflict?

Every ``every`` steps (opt-in, it costs one extra backward per term on probe steps) the probe computes each loss
term's gradient on the parameters of one shared module (e.g. the image backbone or the decoder) and logs:

* ``train/term_grad_norm[term=t]``          gradient norm of term t on the shared parameters
* ``train/term_grad_cos[a=t1,b=t2]``         cosine between two terms' gradients (< 0: they pull in opposite directions)

Loss *values* can be misleading: a small uncertainty NLL term can dominate the gradient, and two terms with
falling losses can fight each other on the shared trunk. Call ``probe(losses, step)`` before ``backward()``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from model_forensic_instrument.losses import _parse, _value
from model_forensic_instrument.torch_monitors import GroupSpec, resolve_groups


class LossGradProbe:
    def __init__(
        self, model: Any, shared: GroupSpec, every: int = 500, terms: Optional[Sequence[str]] = None, max_terms: int = 8
    ) -> None:
        params = resolve_groups(model, {"shared": shared})["shared"]
        self.params = [p for _, p in params]
        self.every = max(1, int(every))
        self.terms = set(terms) if terms else None
        self.max_terms = max_terms

    def _group(self, losses: Mapping[str, Any]) -> Dict[str, Any]:
        """Sum the loss dict into terms (cls, bbox, uncertainty, dn_cls, aux2d_cls …) across decoder layers."""
        grouped: Dict[str, Any] = {}
        for key, v in losses.items():
            if "loss" not in key or not hasattr(v, "requires_grad") or not v.requires_grad:
                continue
            term, _, branch = _parse(key)
            name = f"{branch}_{term}" if branch else term
            if term == "total" or (self.terms is not None and name not in self.terms):
                continue
            grouped[name] = grouped[name] + v if name in grouped else v
        return dict(sorted(grouped.items(), key=lambda kv: -(_value(kv[1]) or 0.0))[: self.max_terms])

    def probe(self, losses: Mapping[str, Any], step: int) -> Dict[str, float]:
        if step % self.every:
            return {}
        import torch

        grads: Dict[str, Any] = {}
        for name, loss in self._group(losses).items():
            gs = torch.autograd.grad(loss, self.params, retain_graph=True, allow_unused=True)
            flat = [g.reshape(-1) for g in gs if g is not None]
            if flat:
                grads[name] = torch.cat(flat).detach().float()
        out: Dict[str, float] = {}
        names: List[str] = sorted(grads)
        for n in names:
            out[f"train/term_grad_norm[term={n}]"] = float(grads[n].norm())
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                na, nb = grads[a].norm(), grads[b].norm()
                if float(na) > 0 and float(nb) > 0:
                    out[f"train/term_grad_cos[a={a},b={b}]"] = float((grads[a] @ grads[b]) / (na * nb))
        return out
