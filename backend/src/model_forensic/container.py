"""Composition root: the only place that wires adapters to ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model_forensic.ai.fake_llm import OfflineHeuristicLLM
from model_forensic.ai.investigator import Investigator
from model_forensic.ai.prompts import PromptLibrary
from model_forensic.analysis.metric_registry import MetricRegistry
from model_forensic.application.ports import LLMProvider
from model_forensic.application.service import ForensicService
from model_forensic.application.study import StudyService
from model_forensic.infrastructure.artifacts.store import ArtifactStore
from model_forensic.infrastructure.connectors import create_connector
from model_forensic.infrastructure.llm.openai_compatible import LLMSettings, OpenAICompatibleProvider
from model_forensic.infrastructure.persistence.sqlite_store import (
    DocumentStore,
    SqliteFindingStore,
    SqliteStudyStore,
)
from model_forensic.infrastructure.search.sqlite_index import SqliteSearchIndex
from model_forensic.knowledge import KnowledgeStore
from model_forensic.knowledge.docindex import DocIndex
from model_forensic.knowledge.sources import discover
from model_forensic.settings import LLMConfig, Settings
from model_forensic.shared.logging import get_logger
from model_forensic.shared.retry import RetryPolicy

log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    service: ForensicService
    docs: DocumentStore
    prompts: PromptLibrary
    llm: LLMProvider
    studies: StudyService

    backend_errors: dict[str, str] = field(default_factory=dict)  # workspace name -> why it is unavailable
    knowledge: KnowledgeStore = field(default_factory=KnowledgeStore)  # team knowledge, independent of runs
    docindex: DocIndex = field(default_factory=DocIndex)  # full-text index of the knowledge sources' documentation
    tasks: Any = None  # application.tasks.TaskManager — every background task, application-scoped
    test_space: Any = None  # application.test_space.TestSpace — experiments → reviewed task-cost proposals
    prefetcher: Any = None  # application.prefetch.PrefetchManager (set by the API)
    llm_status: Any = None  # last connection check of the active LLM (application.llm_connect.ping)
    beliefs: Any = None  # application.beliefs.BeliefService (set by the API; the study book uses its verdicts)
    session_api_key: str | None = None  # BYOK key entered in the UI: memory only, never persisted

    def study_book(self, study_id: str, ai: bool = False, progress: Any = None) -> tuple[Path, Path]:
        """Generate the study book (one self-contained HTML + study_summary.json) under <data_dir>/books/<id>/."""
        from model_forensic.ai.fake_llm import OfflineHeuristicLLM
        from model_forensic.application.ports import ChatMessage
        from model_forensic.reports import study_book

        study = self.studies.get(study_id)
        narrate = None
        if ai and not isinstance(self.llm, OfflineHeuristicLLM):

            def narrate(evidence_text: str, _evid: Any) -> str | None:
                msgs = [
                    ChatMessage(
                        "system",
                        "You write the narrative of a training-forensics study. Use ONLY the "
                        "evidence given; never invent numbers; separate observation from inference; end "
                        "with the two most important next steps. 150-250 words.",
                    ),
                    ChatMessage("user", f"Study: {study.title}\nGoal: {study.goal}\n\nEvidence:\n{evidence_text}"),
                ]
                return self.llm.complete(msgs).content

        chapter_llm = self.llm if ai and not isinstance(self.llm, OfflineHeuristicLLM) else None
        html_text, summary = study_book.build(
            self.service,
            study,
            narrate,
            progress,
            chapter_llm=chapter_llm,
            knowledge=self.knowledge,
            beliefs=self._beliefs_for_book(),
        )
        out = self.settings.paths()["work"] / study_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(html_text, encoding="utf-8")
        (out / "study_summary.json").write_text(study_book.dump_summary(summary), encoding="utf-8")
        return out / "index.html", out / "study_summary.json"

    def _beliefs_for_book(self) -> list[Any]:
        """(belief entry, latest verdict summary) pairs — the book checks each on its comparisons."""
        from model_forensic.application.beliefs import BeliefService

        try:
            b = self.beliefs or BeliefService(self.service, self.knowledge, self.docs)
            out = []
            for item in b.overview():
                entry = next(e for e in b.beliefs() if e.id == item["entry"]["id"])
                out.append((entry, item["verdict"]))
            return out
        except Exception:  # the book never depends on the knowledge files being valid
            return []

    def reload_azure_interface(self) -> None:
        """Re-read config/azure.local.yaml (after Settings → Azure saves it) and swap the Azure workspaces."""
        from model_forensic.interface import azure

        iface = azure.load()
        new = iface.to_backends()
        old_azure = {b.name for b in self.settings.backends if b.kind == "azureml"}
        keep = [b for b in self.settings.backends if b.kind != "azureml"]
        for name in old_azure:
            self.service.sources.pop(name, None)
            self.backend_errors.pop(name, None)
        for b in new:
            try:
                src = create_connector(b.kind, {"name": b.name, **b.options})
                self.service.sources[b.name] = src
            except Exception as exc:
                self.backend_errors[b.name] = f"{type(exc).__name__}: {exc}"
        self.settings.backends = new + keep
        self.settings.me = list(iface.defaults.me)  # `user:me` follows the saved names
        self.settings.notices = [n for n in self.settings.notices if not n.startswith("Azure interface")]
        if iface.placeholders():
            self.settings.notices.append(
                f"Azure interface: fill in {', '.join(iface.placeholders())} in {iface.source}"
            )

    def configure_llm(self, cfg: LLMConfig, api_key: str | None = None) -> None:
        """Swap the LLM provider at runtime (Settings → Providers)."""
        if api_key is not None:
            self.session_api_key = api_key or None
        self.settings.llm = cfg
        self.llm = make_llm(cfg, self.session_api_key)

    def investigator(self) -> Investigator:
        return Investigator(
            self.service,
            self.llm,
            self.prompts,
            max_steps=self.settings.llm.max_tool_steps,
            token_budget=min(24_000, self.settings.llm.context_limit_tokens // 2),
        )


def _me_resolver(settings: Settings) -> Any:
    """Names identifying the current user in runs: `me:` from the config, else the signed-in `az` user (once)."""
    cache: list[list[str]] = []

    def me() -> list[str]:
        from model_forensic.shared import identity

        u = identity.current()
        if u is not identity.LOCAL:  # shared server: whoever is signed in
            return list(dict.fromkeys(x for x in (u.name, u.id) if x))
        if settings.me:
            return list(settings.me)
        if not cache:
            names: list[str] = []
            if any(b.kind == "azureml" for b in settings.backends):
                from model_forensic.interface import azure

                try:
                    u = azure.run_az(
                        [
                            "ad",
                            "signed-in-user",
                            "show",
                            "--query",
                            "{name:displayName, mail:mail, upn:userPrincipalName}",
                        ]
                    )
                    names = [str(v) for v in (u or {}).values() if v]
                except Exception:  # no az / not signed in: `user:me` explains how to set it
                    names = []
            cache.append(names)
        return cache[0]

    return me


def build(settings: Settings) -> Container:
    from model_forensic.application import llm_connect

    saved = llm_connect.load(settings.data_dir)  # what the UI last connected to wins over model-forensic.yaml
    if saved is not None:
        settings.llm = saved
        if saved.local_url and not settings.local_llm_url:
            settings.local_llm_url = saved.local_url  # the local server connected to last time
    d = settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    metrics = MetricRegistry()
    for f in settings.metric_registry_files:
        metrics.load_yaml(f)
    docs = DocumentStore(settings.paths()["index"] / "forensic.sqlite")
    projects = _load_projects(settings)
    sources, errors = [], {}
    for b in settings.backends:
        # One unreachable workspace must not take the whole app down.
        try:
            sources.append(create_connector(b.kind, {"name": b.name, **b.options}))
        except Exception as exc:
            errors[b.name] = f"{type(exc).__name__}: {exc}"
            log.warning("backend unavailable", backend=b.name, error=errors[b.name])
    svc = ForensicService(
        sources=sources,
        index=SqliteSearchIndex(settings.paths()["index"] / "index.sqlite"),
        findings=SqliteFindingStore(docs),
        artifacts=ArtifactStore(
            settings.paths()["artifacts"],
            max_bytes=int(settings.artifact_cache_max_gb * 1024**3) if settings.artifact_cache_max_gb else None,
            min_free_bytes=int(settings.artifact_min_free_gb * 1024**3),
            workers=settings.download_workers,
        ),
        metrics=metrics,
    )
    from model_forensic.application.run_groups import BACKEND as GROUP_BACKEND
    from model_forensic.application.run_groups import GroupSource

    svc.sources[GROUP_BACKEND] = GroupSource(svc)  # group:<run>+<run>: one subject made of several runs
    from model_forensic.analysis.local_plugins import install
    from model_forensic.interface import team

    svc.local_plugins, perrs = install(svc.analyses, list(settings.plugin_dirs))
    settings.notices += [f"Plugin: {e}" for e in perrs]
    svc.me = _me_resolver(settings)
    svc.projects = projects
    for p in projects:  # a project's metric names → the core's, on the workspaces it owns (configuration, not code)
        rules = p.metrics.naming()
        for src in svc.sources.values():
            if src.name in p.backends and (rules.aliases or rules.rules):
                src.naming = rules  # type: ignore[attr-defined]
    if settings.meta_cache_max_gb > 0:
        from model_forensic.infrastructure.persistence.meta_cache import MetaCache

        svc.meta_cache = MetaCache(settings.paths()["meta"], int(settings.meta_cache_max_gb * 1024**3))
    svc.team, notice = team.load()
    if notice:
        settings.notices.append(notice)
    return Container(
        settings=settings,
        service=svc,
        docs=docs,
        prompts=PromptLibrary.load(settings.prompt_dirs),
        llm=make_llm(settings.llm),
        studies=StudyService(SqliteStudyStore(docs), svc),
        backend_errors=errors,
        knowledge=KnowledgeStore(settings.knowledge_dir, discover(settings.knowledge_sources, settings.knowledge_dir)),
        docindex=DocIndex(settings.paths()["index"] / "docs.sqlite"),
        tasks=_task_manager(docs),
    )


def _task_manager(docs: Any) -> Any:
    """Task costs: the shipped master configuration, overlaid by reviewed local changes (config/tasks.local.yaml)."""
    from model_forensic.application.tasks import TaskManager, load_specs
    from model_forensic.shared.paths import home_path

    specs = load_specs(Path(__file__).parent / "defaults" / "tasks.yaml", home_path("config/tasks.local.yaml"))
    return TaskManager(specs, docs, workers=8)


def _load_projects(settings: Settings) -> list[Any]:
    """Project profiles (config/projects/*.local.yaml): add their local run folders as workspaces and their
    documentation folders as knowledge sources; problems become notices, never a failed start."""
    from model_forensic.knowledge.sources import KnowledgeSource
    from model_forensic.projects import load_profiles
    from model_forensic.settings import BackendConfig
    from model_forensic.shared.paths import project_path

    profiles, notes = load_profiles(settings.projects_dir)
    settings.notices += [f"Project: {n}" for n in notes]
    have = {b.name for b in settings.backends}
    known_sources = {k.name for k in settings.knowledge_sources}
    for p in profiles:
        for lr in p.local_roots:
            if lr.name not in have:
                settings.backends.append(
                    BackendConfig(kind="local", name=lr.name, options={"root": str(project_path(lr.root))})
                )
                have.add(lr.name)
        missing = [w for w in p.workspaces if w not in have]
        if missing:
            settings.notices.append(f"Project {p.id}: workspace(s) {', '.join(missing)} not configured")
        for i, path in enumerate(p.knowledge_sources):
            name = f"{p.id}-docs{i + 1 if i else ''}"
            if name not in known_sources:
                settings.knowledge_sources.append(KnowledgeSource(name=name, path=path, codebase=p.id))
                known_sources.add(name)
    return profiles


def make_llm(c: LLMConfig, api_key: str | None = None) -> LLMProvider:
    if c.enabled:
        return OpenAICompatibleProvider(
            LLMSettings(
                base_url=c.base_url,
                model=c.model,
                api_key_env=c.api_key_env,
                temperature=c.temperature,
                max_tokens=c.max_tokens,
                timeout_s=c.timeout_s,
                context_limit_tokens=c.context_limit_tokens,
                reasoning_effort=c.reasoning_effort,
                azure_api_version=c.azure_api_version,
                retry=RetryPolicy(max_attempts=3),
                max_concurrency=c.max_concurrency,
            ),
            api_key=api_key,
        )
    return OfflineHeuristicLLM()
