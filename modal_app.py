from pathlib import Path

import modal

APP_NAME = "guardmeta-engine"
ROOT = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    .add_local_python_source("app")
    .add_local_dir(ROOT / "app" / "static", remote_path="/root/app/static")
)

app = modal.App(APP_NAME, image=image)
secret = modal.Secret.from_name(
    "meta-hackathon-secret",
    required_keys=["MODEL_API_KEY", "GUARDMETA_API_KEY"],
)
model_secret = modal.Secret.from_name("guardmeta-open-model")


@app.function(
    secrets=[secret, model_secret],
    env={
        "ENVIRONMENT": "production",
        "MODEL_PROVIDER": "modal",
        "OPEN_WEIGHT_BASE_URL": "https://yemiog--ep-muse-spark-open-816b-a42b-server.us-west.modal.direct/v1",
        "OPEN_WEIGHT_MODEL": "meta-models/Muse-Spark-Open-816B-A42B",
        "REQUIRE_OPEN_WEIGHT_MODEL": "true",
        # Promoted after the 2026-10-28 production benchmark passed 5/5.
        "UPSTREAM_OPERATIONAL_VERIFIED": "true",
        "DEMO_MODE": "true",
        "DEMO_RATE_LIMIT_PER_MINUTE": "6",
    },
    timeout=180,
    memory=1024,
    max_containers=1,
)
@modal.asgi_app()
def fastapi_app():
    from app.main import create_app

    return create_app()
