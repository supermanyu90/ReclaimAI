# ADR-001: Cloud SQL for PostgreSQL as the operational database

Status: Accepted · 2026-10-15

## Context
ReclaimAI needs transactional integrity for asset versions, confirmations, grants,
handover approvals and the outbox. The brief names Cloud SQL, with AlloyDB as an alternative.

## Decision
Use PostgreSQL 16 locally and Cloud SQL for PostgreSQL in cloud. Schema changes only via
Alembic. Use `pgcrypto` for UUIDs. BigQuery holds analytical copies, never the source of truth.

## Alternatives
- **AlloyDB for PostgreSQL:** wire-compatible; higher baseline cost; revisit if read scale or
  analytical query load on the operational DB justifies it. Migration would be a connection
  change plus a data move, so do not provision it alongside Cloud SQL.
- **Graph database for the evidence graph:** rejected; a SQL `relationships` table suffices.

## Consequences
Postgres-specific features (e.g. `SELECT … FOR UPDATE`, partial unique indexes) are allowed.
