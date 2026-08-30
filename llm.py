"""
Unified LLM abstraction layer.

Every file that needs to call an AI model (ask.py, server.py, ingest.py)
imports from here instead of importing a provider SDK directly. This
module reads AI_PROVIDER from config.py and routes calls to the right
backend -- Anthropic, OpenAI, Google, Mistral, or a local Ollama server.

Usage:
    from llm import chat, get_model, get_tag_model

    answer = chat("What happened last Tuesday?", model=get_model())
    tags   = chat(tagging_prompt, model=get_tag_model())

All providers are normalized to the same interface: you pass a string
prompt (or a list of message dicts) and get a string back. Provider
SDKs are imported lazily so you only need the one you're actually using
installed.
"""

import config


def get_model():
    """Returns the model name for Q&A based on the active provider."""
    p = config.AI_PROVIDER
    if p == "anthropic":
        return config.CLAUDE_MODEL
    elif p == "openai":
        return config.OPENAI_MODEL
    elif p == "google":
        return config.GEMINI_MODEL
    elif p == "mistral":
        return config.MISTRAL_MODEL
    elif p == "ollama":
        return config.OLLAMA_MODEL
    return config.CLAUDE_MODEL  # fallback


def get_tag_model():
    """Returns the cheapest model for the active provider (used for tagging)."""
    return config.TAG_EXTRACTION_MODEL


def _ensure_messages(prompt_or_messages):
    """Normalize input to a list of message dicts."""
    if isinstance(prompt_or_messages, str):
        return [{"role": "user", "content": prompt_or_messages}]
    return list(prompt_or_messages)


# ---------------------------------------------------------------------------
# Provider-specific backends (lazy imports)
# ---------------------------------------------------------------------------

def _chat_anthropic(messages, model, max_tokens):
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    usage = getattr(response, "usage", None)
    return text, usage


def _chat_openai(messages, model, max_tokens):
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    return text, usage


def _chat_google(messages, model, max_tokens):
    import google.generativeai as genai
    genai.configure(api_key=config.GOOGLE_API_KEY)
    gmodel = genai.GenerativeModel(model)
    # Gemini uses a different message format -- flatten to a single prompt
    # for simplicity (multi-turn is handled by the caller building the
    # messages list, but Gemini's SDK wants its own format).
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        prefix = "" if role == "user" else f"[{role}] "
        parts.append(f"{prefix}{msg['content']}")
    combined = "\n\n".join(parts)
    response = gmodel.generate_content(
        combined,
        generation_config={"max_output_tokens": max_tokens},
    )
    text = response.text or ""
    # Gemini doesn't expose usage the same way; return None
    return text, None


def _chat_mistral(messages, model, max_tokens):
    from mistralai import Mistral
    client = Mistral(api_key=config.MISTRAL_API_KEY)
    response = client.chat.complete(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    return text, usage


def _chat_ollama(messages, model, max_tokens):
    import urllib.request
    import json
    base = config.OLLAMA_BASE_URL.rstrip("/")
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    text = data.get("message", {}).get("content", "")
    return text, None


_BACKENDS = {
    "anthropic": _chat_anthropic,
    "openai": _chat_openai,
    "google": _chat_google,
    "mistral": _chat_mistral,
    "ollama": _chat_ollama,
}


def chat(prompt_or_messages, model=None, max_tokens=1000, provider=None):
    """
    Send a prompt (string or message list) to the configured AI provider.

    Returns (text, usage) where text is the response string and usage is
    provider-specific usage info (or None if the provider doesn't report it).
    """
    provider = provider or config.AI_PROVIDER
    model = model or get_model()
    messages = _ensure_messages(prompt_or_messages)

    backend = _BACKENDS.get(provider)
    if not backend:
        raise ValueError(
            f"Unknown AI provider '{provider}'. "
            f"Supported: {', '.join(_BACKENDS.keys())}"
        )

    return backend(messages, model, max_tokens)
