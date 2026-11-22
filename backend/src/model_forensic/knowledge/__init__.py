"""Knowledge block: what the team has learnt, independent of any run.

Entries say *when* they apply (setup, dataset, stage, config values, tags) and *what* that changes (metrics and
classes to set aside, a caveat, a setup label), with their source and confidence. Reports consume the resolved effects
and always say which entry set something aside. The block depends on nothing run-specific except plain context values,
so it stays useful whatever backend or codebase the runs come from (see ``harness/`` for filling it from a codebase).
"""

from model_forensic.knowledge.engine import Applied, RunContext, resolve
from model_forensic.knowledge.model import Effects, KnowledgeEntry, When
from model_forensic.knowledge.store import KnowledgeStore

__all__ = ["Applied", "Effects", "KnowledgeEntry", "KnowledgeStore", "RunContext", "When", "resolve"]
