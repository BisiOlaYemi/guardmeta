from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import get_settings
from app.eligibility import evaluate_eligibility
from app.exceptions import GuardMetaError
from app.gateway.proxy import router as gateway_router
from app.logging import configure_logging
from app.middleware import InboundAuthMiddleware, InMemoryRateLimiter, RequestContextMiddleware
from app.treasury.api import router as treasury_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(
        title="GuardMeta Engine",
        description="Deterministic logic and guardrail proxy for Meta Model API.",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    application.add_middleware(InMemoryRateLimiter, limit=settings.rate_limit_per_minute)
    application.add_middleware(InboundAuthMiddleware)
    application.add_middleware(RequestContextMiddleware)

    @application.exception_handler(GuardMetaError)
    async def handle_guardmeta(_: Request, exc: GuardMetaError) -> JSONResponse:
        return JSONResponse(exc.to_payload(), status_code=exc.status_code)

    @application.get("/health")
    def health_check() -> dict[str, str]:
        return {
            "status": "operational",
            "model": settings.model_name,
            "version": __version__,
            "environment": settings.environment,
        }

    @application.get("/ready")
    def ready() -> dict[str, object]:
        evidence = evaluate_eligibility(settings)
        key_ready = settings.has_upstream_key or evidence.provider == "self_hosted"
        eligibility_ready = evidence.eligible or not settings.require_open_weight_model
        operational_ready = (
            settings.upstream_operational_verified or settings.environment != "production"
        )
        return {
            "ready": key_ready and eligibility_ready and operational_ready,
            "configured": key_ready and eligibility_ready,
            "upstream_operational_verified": settings.upstream_operational_verified,
            "upstream": settings.meta_base_url,
            "model": settings.model_name,
            "provider": evidence.provider,
            "hosted_meta_api": evidence.hosted_meta_api,
            "eligibility": evidence.to_dict(),
            "inbound_auth": settings.inbound_auth_required,
            "public_demo": settings.demo_mode,
        }

    @application.get("/")
    def demo() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(gateway_router)
    application.include_router(treasury_router)
    return application


app = create_app()
