# ADR-0002: Modular monolith

Status: accepted

## Context
Single-user workbench first, small-team server later. Microservices would add deployment and
consistency cost with no current benefit.

## Decision
One Python process (API + background jobs via threads) and one static web app. Module boundaries
are enforced by the dependency rule; ports are the seams for future extraction (sync worker,
analysis workers, download workers).

## Consequences
+ One-command local startup; trivial debugging.
− Background jobs are in-process; a crash loses in-flight jobs (acceptable: jobs are idempotent and resumable).
