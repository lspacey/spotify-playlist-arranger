"""LLM client initialization and chat API."""

import os
import sys
import logging
import re

from playlist_arranger import config as _config

# Convenience aliases for constants that never change at runtime
LLM_BACKEND = _config.LLM_BACKEND
OLLAMA_BASE_URL = _config.OLLAMA_BASE_URL
DEEPSEEK_API_KEY = _config.DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL = _config.DEEPSEEK_BASE_URL
MISTRAL_API_KEY = _config.MISTRAL_API_KEY
MISTRAL_BASE_URL = _config.MISTRAL_BASE_URL

logger = logging.getLogger(__name__)

try:
    import ollama as _ollama

    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    from openai import OpenAI as _OpenAI

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

_llm_clients: dict = {}  # keyed by backend name (ollama / deepseek / mistral)
_llm_models_used: dict = {}  # model name per backend
# Legacy references kept for backward compat with any external code that
# may read these module-level names directly:
_llm_client = None  # deprecated — use _llm_clients[backend] instead
_llm_backend_used = None  # deprecated — use the backend key directly
_llm_model_used = None  # deprecated — use _llm_models_used[backend] instead


def _resolve_model(backend: str, model_override: str | None) -> str:
    """Return the model name for *backend*, preferring override then settings.json then .env."""
    if model_override:
        return model_override
    s = _config.load_settings()
    model_map = {
        "ollama": s.ollama_model,
        "deepseek": s.deepseek_model,
        "mistral": s.mistral_model,
    }
    return model_map.get(backend, s.ollama_model)


def _init_llm_client(backend=None, model_override=None):
    """Initialize the LLM client based on LLM env var: ollama | deepseek | mistral.

    All model names flow through _resolve_model() — settings.json wins if
    non-empty, .env provides the fallback.  Clients are cached per-backend
    (dict keyed by backend name) so that switching backends via the anchors
    page dropdown reuses previously-initialised clients rather than
    creating new ones on every call.

    Args:
        backend: Override backend name (None = use settings.json llm_backend).
        model_override: Override model name (None = resolve from settings/.env).
    """
    global _llm_clients, _llm_models_used
    global _llm_client, _llm_backend_used, _llm_model_used

    if backend is None:
        # backend NOT provided — read from settings.json (with .env fallback).
        s = _config.load_settings()
        backend = s.llm_backend or LLM_BACKEND

    if backend is None:  # still None after settings lookup — bail
        raise RuntimeError("No LLM backend configured.")

    model = _resolve_model(backend, model_override)

    # Per-backend cache: reuse client if this exact backend+model was
    # previously initialised.  Model-only changes (same backend, different
    # model e.g. mistral-large → mistral-medium) must rebuild.
    cached_client = _llm_clients.get(backend)
    cached_model = _llm_models_used.get(backend)
    if cached_client is not None and cached_model == model:
        # Update legacy refs for backward compat (llm_chat reads these)
        _llm_client = cached_client
        _llm_backend_used = backend
        _llm_model_used = model
        return cached_client

    if backend == "ollama":
        if not HAS_OLLAMA:
            raise ImportError(
                "ollama package not installed: pip install ollama"
            )
        # Build an explicit Client with the configured host — never rely
        # on the library's default client (which reads OLLAMA_HOST once at
        # import time, before we have a chance to set it).
        _ollama_client = _ollama.Client(host=OLLAMA_BASE_URL)

        # Debug: log proxy env vars to rule out proxy-interception issues
        http_proxy = os.environ.get("HTTP_PROXY", "")
        https_proxy = os.environ.get("HTTPS_PROXY", "")
        no_proxy = os.environ.get("NO_PROXY", "")
        logger.debug(
            "Ollama client connecting to %s (HTTP_PROXY=%r, HTTPS_PROXY=%r, NO_PROXY=%r)",
            OLLAMA_BASE_URL, http_proxy, https_proxy, no_proxy,
        )

        # Verify model exists locally — distinguish ConnectionError from missing model
        try:
            _ollama_client.show(model)
        except Exception as exc:
            err_msg = str(exc).lower()
            if "connection" in err_msg or "connect" in err_msg or "refused" in err_msg:
                raise RuntimeError(
                    f"Could not connect to Ollama server — check it's running "
                    f"and OLLAMA_BASE_URL={OLLAMA_BASE_URL} matches your local instance"
                ) from exc
            raise RuntimeError(
                f"Model '{model}' not found locally. "
                f"Please run: ollama pull {model}"
            ) from exc
        logger.info("Using Ollama — model: %s", model)
        _llm_backend_used = "ollama"
        _llm_model_used = model
        _llm_client = _ollama_client
        _llm_clients["ollama"] = _ollama_client
        _llm_models_used["ollama"] = model

    elif backend == "deepseek":
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed: pip install openai"
            )
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not set in environment")
        logger.info("Using DeepSeek API — model: %s", model)
        _llm_backend_used = "deepseek"
        _llm_model_used = model
        _llm_client = _OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        _llm_clients["deepseek"] = _llm_client
        _llm_models_used["deepseek"] = model

    elif backend == "mistral":
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed: pip install openai"
            )
        if not MISTRAL_API_KEY:
            raise RuntimeError("MISTRAL_API_KEY not set in environment")
        logger.info("Using Mistral API — model: %s", model)
        _llm_backend_used = "mistral"
        _llm_model_used = model
        _llm_client = _OpenAI(
            api_key=MISTRAL_API_KEY,
            base_url=MISTRAL_BASE_URL,
        )
        _llm_clients["mistral"] = _llm_client
        _llm_models_used["mistral"] = model

    else:
        raise RuntimeError(
            f"Unknown LLM backend: {backend}. Use ollama | deepseek | mistral"
        )

    return _llm_client


def llm_chat(system_msg, user_msg, temperature=0.7, max_tokens=300) -> str:
    """Send a chat request to the configured LLM backend. Returns response text."""
    if _llm_client is None:
        _init_llm_client()

    # Use the ACTUALLY INITIALIZED backend + model, not the env-var defaults.
    backend = _llm_backend_used or LLM_BACKEND
    model = _llm_model_used or ""

    try:
        if backend == "ollama":
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
            try:
                kwargs["think"] = False
                resp = _llm_client.chat(**kwargs)
            except TypeError:
                # `think` parameter not supported by some models/versions
                del kwargs["think"]
                resp = _llm_client.chat(**kwargs)
            except Exception as e:
                raise RuntimeError(f"Ollama API error: {e}") from e
            raw = resp.get("message", {}).get("content", "")
            if not raw:
                return ""
            raw = raw.strip()
            # strip if present
            text = re.sub(
                r"", "", raw, flags=re.DOTALL
            ).strip()
            if not text:
                m = re.search(
                    r"", raw, flags=re.DOTALL
                )
                text = m.group(1).strip() if m else raw
            return text

        elif backend in ("deepseek", "mistral"):
            try:
                resp = _llm_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                raise RuntimeError(
                    f"{backend.title()} API error: {e}"
                ) from e
            return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"LLM error ({backend}): {e}") from e

    return ""
