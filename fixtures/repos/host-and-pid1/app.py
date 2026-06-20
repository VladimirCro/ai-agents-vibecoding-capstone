"""Minimal FastAPI-shaped fixture app. NO real secret values — names only."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL")


def create_app():
    return {"/health": lambda: {"status": "ok"}, "/ready": lambda: {"status": "ready"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:create_app", host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
