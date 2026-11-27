"""HTTP routes: /tracks — continuous training lines (a workspace + an experiment) followed over time."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from model_forensic.api.routes.context import RouteContext
from model_forensic.application.tracks import TrackService
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    p = ctx.p
    tracks = TrackService(ctx.container.docs, ctx.svc)

    @app.get(f"{p}/tracks")
    def tracks_list(progress: bool = False) -> dict[str, Any]:
        """The tracks you follow; ``progress=true`` adds each one's latest state and sentence (for Today)."""
        out = []
        for t in tracks.all():
            if progress:
                pr = tracks.progress(t["id"])
                t = {
                    **t,
                    "state": pr["state"],
                    "sentence": pr["sentence"],
                    "latest": pr["latest"],
                    "health": pr["health"],
                }
            out.append(t)
        return {"tracks": out}

    @app.post(f"{p}/tracks")
    def tracks_create(body: dict[str, Any]) -> dict[str, Any]:
        """Follow an experiment of a workspace: ``workspace``, ``experiment``, optional ``title``, ``metrics``."""
        t = tracks.create(body, identity.current().id)
        ctx.container.docs.audit(identity.current().id, "track.create", {"id": t["id"]})
        return t

    @app.get(f"{p}/tracks/{{track_id}}")
    def tracks_progress(track_id: str) -> dict[str, Any]:
        """The track's runs oldest first, each vs the previous run and the best so far (noise-aware), its health."""
        return tracks.progress(track_id)

    @app.put(f"{p}/tracks/{{track_id}}")
    def tracks_update(track_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return tracks.update(track_id, body)

    @app.delete(f"{p}/tracks/{{track_id}}")
    def tracks_delete(track_id: str) -> dict[str, Any]:
        tracks.delete(track_id)
        ctx.container.docs.audit(identity.current().id, "track.delete", {"id": track_id})
        return {"deleted": track_id}
