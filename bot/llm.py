"""OpenAI-compatible chat completion with a fallback chain: NVIDIA -> OpenRouter -> local Ollama."""
import logging
import os

import httpx

log = logging.getLogger("bot.llm")
NVIDIA_MODELS = "nvidia/nemotron-3-super-120b-a12b,openai/gpt-oss-20b"


def providers() -> list[dict]:
    """Configured providers in fallback order. A provider without its key/URL is skipped."""
    out = []
    if os.getenv("NVIDIA_API_KEY"):  # NVIDIA_MODEL may list several, comma-separated; models get retired often
        for model in os.getenv("NVIDIA_MODEL", NVIDIA_MODELS).split(","):
            out.append({"name": f"nvidia:{model.strip()}", "base": "https://integrate.api.nvidia.com/v1",
                        "key": os.environ["NVIDIA_API_KEY"], "model": model.strip(),
                        # thinking off: these are reasoning models, a chat reply doesn't need it (0.6 s vs 60 s+)
                        "extra": {"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}}})
    if os.getenv("OPENROUTER_API_KEY"):
        out.append({"name": "openrouter", "base": "https://openrouter.ai/api/v1",
                    "key": os.environ["OPENROUTER_API_KEY"],
                    "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")})
    if os.getenv("OLLAMA_URL"):
        out.append({"name": "ollama", "base": os.environ["OLLAMA_URL"].rstrip("/") + "/v1", "key": "ollama",
                    "model": os.getenv("OLLAMA_MODEL", "llama3.1")})
    return out


async def complete(messages: list[dict], temperature: float = 0.8, max_tokens: int = 400) -> str:
    """First provider that answers wins. 429/5xx/timeouts move on to the next one."""
    errors = []
    async with httpx.AsyncClient(timeout=30) as client:
        for p in providers():
            try:
                r = await client.post(f"{p['base']}/chat/completions",
                                      headers={"Authorization": f"Bearer {p['key']}"},
                                      json={"model": p["model"], "messages": messages,
                                            "temperature": temperature, "max_tokens": max_tokens, **p.get("extra", {})})
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"] or ""
                if text.strip():
                    return text.strip()
                raise ValueError("empty reply")
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("LLM %s failed: %s", p["name"], e)
                errors.append(f"{p['name']}: {e!r}")
    raise RuntimeError("all LLM providers failed: " + "; ".join(errors) if errors else "no LLM provider configured")
