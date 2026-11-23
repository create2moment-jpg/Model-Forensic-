"""What the route areas share (design review D7): set up once in app.py::create_app, then handed to every area.

Fields without a default are filled in create_app; the ``init=False`` ones are filled by the area that creates them
(the order of areas is the original registration order, so a later area always finds them set).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from model_forensic.api.common import BuildInfo, JobRegistry
from model_forensic.application.drafts import DraftService
from model_forensic.application.prefetch import PrefetchManager
from model_forensic.application.service import ForensicService
from model_forensic.application.study import StudyService
from model_forensic.container import Container
from model_forensic.settings import ServerConfig


@dataclass
class RouteContext:
    container: Container
    svc: ForensicService
    jobs: JobRegistry
    p: str  # the API prefix, /api/v1
    started: float
    server: ServerConfig
    studies: StudyService
    sdump: Callable[[Any], dict[str, Any]]
    _knowledge_for: Callable[..., Any]
    _pending: Callable[..., dict[str, Any]]
    # set by the areas while they register
    prefetcher: PrefetchManager = field(init=False)
    drafts: DraftService = field(init=False)
    build: BuildInfo = field(init=False)
    download: Callable[..., dict[str, Any]] = field(init=False)
    study_book: Callable[..., dict[str, Any]] = field(init=False)
    report: Callable[..., Any] = field(init=False)
