"""LLM client initialization and chat API."""

import os
import sys
import logging
import re

from playlist_arranger.config import (
    LLM_BACKEND,
    OLLAMA_MODEL,
    OLLAMA_API_KEY,
    DEEPSEEK_MODEL,
    DEEPSEEK_API_KEY,
    MISTRAL_MODEL,
    MISTRAL_API_KEY,
)

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

_llm_client = None
_llm_backend_used = None


def _init_llm_client():
    """Initialize the LLM client based on LLM env var: ollama | deepseek | mistral."""
    global _llm_client, _llm_backend_used
    if _llm_client is not None:
        return _llm_client

    backend = LLM_BACKEND

    if backend == "ollama":
        if not HAS_OLLAMA:
            raise ImportError(
                "ollama package not installed: pip install ollama"
            )
        # Verify model exists locally
        try:
            _ollama.show(OLLAMA_MODEL)
        except Exception:
            raise RuntimeError(
                f"Model '{OLLAMA_MODEL}' not found locally. "
                f"Please run: ollama pull {OLLAMA_MODEL}"
            )
        logger.info(f"Using Ollama — model: {OLLAMA_MODEL}")
        _llm_backend_used = "ollama"
        _llm_client = "ollama"  # ollama module is used directly

    elif backend == "deepseek":
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed: pip install openai"
            )
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not set in environment")
        logger.info(f"Using DeepSeek API — model: {DEEPSEEK_MODEL}")
        _llm_backend_used = "deepseek"
        _llm_client = _OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

    elif backend == "mistral":
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed: pip install openai"
            )
        if not MISTRAL_API_KEY:
            raise RuntimeError("MISTRAL_API_KEY not set in environment")
        logger.info(f"Using Mistral API — model: {MISTRAL_MODEL}")
        _llm_backend_used = "mistral"
        _llm_client = _OpenAI(
            api_key=MISTRAL_API_KEY,
            base_url="https://api.mistral.ai/v1",
        )

    else:
        raise RuntimeError(
            f"Unknown LLM backend: {backend}. Use ollama | deepseek | mistral"
        )

    return _llm_client


def llm_chat(system_msg, user_msg, temperature=0.7, max_tokens=300) -> str:
    """Send a chat request to the configured LLM backend. Returns response text."""
    backend = LLM_BACKEND

    if _llm_client is None:
        _init_llm_client()

    try:
        if backend == "ollama":
            kwargs = {
                "model": OLLAMA_MODEL,
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
                resp = _ollama.chat(**kwargs)
            except TypeError:
                # `think` parameter not supported by some models/versions
                del kwargs["think"]
                resp = _ollama.chat(**kwargs)
            except Exception as e:
                raise RuntimeError(f"Ollama API error: {e}") from e
            raw = resp.get("message", {}).get("content", "")
            if not raw:
                return ""
            raw = raw.strip()
            # strip <think>...</think> if present
            text = re.sub(
                r"<think>.*?</think>", "", raw, flags=re.DOTALL
            ).strip()
            if not text:
                m = re.search(
                    r"<think>(.*?)</think>", raw, flags=re.DOTALL
                )
                text = m.group(1).strip() if m else raw
            return text

        elif backend in ("deepseek", "mistral"):
            model = DEEPSEEK_MODEL if backend == "deepseek" else MISTRAL_MODEL
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