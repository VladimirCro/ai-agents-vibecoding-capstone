#!/bin/bash
# entrypoint.sh — exec-form entrypoint for worknote-ai-like fixture
# This is referenced by CMD ["/usr/local/bin/entrypoint.sh"] in the Dockerfile
exec uvicorn app:create_app --host 0.0.0.0 --port "${PORT:-8080}"
