# ADR-0003: Frozen pydantic v2 models for the domain

Status: accepted

## Context
Domain objects must be strongly typed, immutable, validated, versioned, and serializable to JSON
for the API, persistence and AI context.

## Decision
Domain entities subclass a frozen `DomainModel` (pydantic v2, `extra="forbid"`). Persisted roots
carry `schema_version`.

## Consequences
+ One definition gives validation, JSON schema (UI forms for analysis params), and serialization.
− The domain depends on pydantic. Accepted: pydantic is infrastructure-neutral and ubiquitous; it
  does not leak vendor concepts.
