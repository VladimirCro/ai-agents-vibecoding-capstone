"""Minimal FastAPI-shaped fixture app. NO real secret values — names only."""
import os

# Uses Vertex AI
VERTEX_REGION = os.environ.get('VERTEX_REGION')


def create_app():
    return {"/health": lambda: {"status": "ok"}, "/ready": lambda: {"status": "ready"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:create_app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
