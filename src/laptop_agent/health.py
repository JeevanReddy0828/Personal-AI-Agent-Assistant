from __future__ import annotations

from typing import Any


def system_health(orchestrator: Any, llm_reachable: bool | None, config: Any) -> dict[str, object]:
    """A production self-check: is the brain, memory, and mail wired and reachable?

    Pure and side-effect free so it can be unit tested. `llm_reachable` is a cached
    result (None = not applicable / not yet checked) supplied by the caller, so the
    health endpoint never blocks on a live network ping.
    """
    provider = getattr(getattr(orchestrator, "planner", None), "provider", None)
    llm_configured = provider is not None and "OpenAI" in type(provider).__name__

    context = getattr(orchestrator, "context", None)
    obsidian = getattr(context, "obsidian", None)
    vault_connected = bool(obsidian.available()) if obsidian is not None else False

    email_configured = bool(
        getattr(config, "imap_host", None)
        and getattr(config, "imap_username", None)
        and getattr(config, "imap_password", None)
    )

    if not llm_configured:
        overall = "setup"  # no AI connected — first-run state
    elif llm_reachable is False:
        overall = "degraded"  # configured but the endpoint is unreachable
    else:
        overall = "ok"

    # Per-tier reachability from real chat turns (fast/smart/ultra), so the UI can
    # show "the advanced model is busy" even while the fast tier is healthy.
    status = getattr(orchestrator, "model_status", None)
    tier_status = status.snapshot() if status is not None else {"tiers": {}, "degraded": False}

    return {
        "overall": overall,
        "llm": {
            "configured": llm_configured,
            "reachable": llm_reachable,
            "provider": "openai-compatible" if llm_configured else "heuristic",
            "tiers": tier_status["tiers"],
            "degraded_tier": tier_status["degraded"],
        },
        "models": {
            "fast": getattr(config, "llm_model", None),
            "smart": getattr(config, "llm_smart_model", None),
            "ultra": getattr(config, "llm_ultra_model", None),
            "vision": getattr(config, "llm_vision_model", None),
            "openrouter": getattr(config, "openrouter_model", None) if getattr(config, "openrouter_api_key", None) else None,
        },
        "vault": {"connected": vault_connected, "path": getattr(config, "obsidian_vault", None)},
        "email": {"configured": email_configured},
        "search": {
            "provider": getattr(config, "search_provider", "") or "duckduckgo",
            "api_key": bool(getattr(config, "search_api_key", None)),
        },
        "smart_planner": getattr(orchestrator, "smart_planner", None) is not None,
        "vision_planner": getattr(orchestrator, "vision_planner", None) is not None,
    }
