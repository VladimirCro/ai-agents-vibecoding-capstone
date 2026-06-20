"""
worknote-ai-like fixture: minimal app file referencing secrets via os.environ.

This file is a FIXTURE for testing LaunchGuard's repo scanner.
It does NOT contain actual secret values — only environment variable NAMES.
"""
import os

# Application secrets — loaded from environment at runtime
# Cloud Run injects these from Secret Manager via secretKeyRef in service.yaml
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
SES_SMTP_USERNAME = os.environ.get("SES_SMTP_USERNAME")
SES_SMTP_PASSWORD = os.environ.get("SES_SMTP_PASSWORD")
SES_SMTP_HOST = os.environ.get("SES_SMTP_HOST")
SENTRY_DSN_BACKEND = os.environ.get("SENTRY_DSN_BACKEND")
LITELLM_AZURE_API_KEY = os.environ.get("LITELLM_AZURE_API_KEY")
LITELLM_AZURE_ENDPOINT = os.environ.get("LITELLM_AZURE_ENDPOINT")
LITELLM_VERTEX_CREDENTIALS = os.environ.get("LITELLM_VERTEX_CREDENTIALS")
CLAMAV_FUNCTION_URL = os.environ.get("CLAMAV_FUNCTION_URL")

# Non-secret config
PORT = int(os.environ.get("PORT", "8080"))
ENV = os.environ.get("ENV", "development")


def create_app():
    """Minimal app factory for fixture testing."""
    # Health probe route
    routes = {
        "/health": lambda: {"status": "ok"},
        "/ready": lambda: {"status": "ready"},
    }
    return routes


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:create_app", host="0.0.0.0", port=PORT)
