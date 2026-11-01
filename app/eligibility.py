from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from app.config import Settings


_OPEN_WEIGHT_MODEL_MARKERS = {
    "muse-spark-open": "Muse Spark Open",
    "muse-glimmer": "Muse Glimmer",
}

_ELIGIBLE_PROVIDERS = {
    "modal",
    "baseten",
    "fireworks",
    "together",
    "self_hosted",
}


@dataclass(frozen=True)
class EligibilityEvidence:
    eligible: bool
    enforcement_enabled: bool
    model: str
    model_family: str | None
    open_weight: bool
    provider: str
    hosted_meta_api: bool
    endpoint_host: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_eligibility(settings: Settings) -> EligibilityEvidence:
    """Build judge-visible evidence from runtime configuration, without exposing secrets."""

    model_normalized = settings.model_name.lower().replace("_", "-")
    model_family = next(
        (title for marker, title in _OPEN_WEIGHT_MODEL_MARKERS.items() if marker in model_normalized),
        None,
    )
    endpoint_host = (urlparse(settings.meta_base_url).hostname or "local").lower()
    hosted_meta_api = endpoint_host == "api.meta.ai" or endpoint_host.endswith(".api.meta.ai")
    provider = _resolve_provider(settings.model_provider, endpoint_host)
    open_weight = model_family is not None

    if hosted_meta_api:
        eligible = False
        reason = "Meta Model API hosts the model; it is not an open-weight partner deployment."
    elif not open_weight:
        eligible = False
        reason = "Configured model is not a recognized eligible Meta open-weight model."
    elif provider not in _ELIGIBLE_PROVIDERS:
        eligible = False
        reason = "Serving provider is not declared as an eligible open-weight infrastructure provider."
    else:
        eligible = True
        reason = f"{model_family} open weights are served by {provider}."

    return EligibilityEvidence(
        eligible=eligible,
        enforcement_enabled=settings.require_open_weight_model,
        model=settings.model_name,
        model_family=model_family,
        open_weight=open_weight,
        provider=provider,
        hosted_meta_api=hosted_meta_api,
        endpoint_host=endpoint_host,
        reason=reason,
    )


def _resolve_provider(configured: str, endpoint_host: str) -> str:
    explicit = configured.strip().lower().replace("-", "_")
    if explicit and explicit != "auto":
        return explicit
    if endpoint_host == "api.meta.ai" or endpoint_host.endswith(".api.meta.ai"):
        return "meta_hosted"
    if endpoint_host.endswith("modal.run") or endpoint_host.endswith("modal.com"):
        return "modal"
    if endpoint_host.endswith("baseten.co"):
        return "baseten"
    if endpoint_host.endswith("fireworks.ai"):
        return "fireworks"
    if endpoint_host.endswith("together.xyz"):
        return "together"
    if endpoint_host in {"local", "localhost", "127.0.0.1", "::1"}:
        return "self_hosted"
    return "unknown"
