-- LaunchGuard — Postgres init script
-- Runs automatically on first container start via docker-entrypoint-initdb.d.
-- Enables pgvector so the launchguard_memory schema can use vector(768) columns.
-- Safe to run multiple times (IF NOT EXISTS).

CREATE EXTENSION IF NOT EXISTS vector;
