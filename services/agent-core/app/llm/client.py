"""Model provider client — real HTTP calls only, no canned responses.

Three upstream protocols, dispatched on provider.format; callers (agent
binding, task runner) never see the difference:

  chat_completions — POST {base}/chat/completions, GET {base}/models
                     (OpenAI-compatible: DeepSeek / OpenAI / 通义 / 智谱 /
                     月之暗面 / OpenRouter / Ollama / Gemini 兼容端点 …)
  responses        — POST {base}/responses, GET {base}/models
                     (OpenAI Responses API; request/response shapes are
                     translated here: messages -> instructions + input,
                     output[] -> text, usage field names mapped)
  anthropic        — POST {base}/v1/messages, GET {base}/v1/models
                     (Claude Messages; x-api-key + anthropic-version
                     headers, system prompt lifted out of messages)

Any failure raises LLMError so the caller records an honest MODEL_ERROR.
"""
import httpx

class LLMError(Exception):
    error_type = "MODEL_ERROR"

FORMATS = ("chat_completions", "responses", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"
# Anthropic requires max_tokens on every Messages request; the internal
# call sites never set one (Chat Completions leaves it optional), so the
# Anthropic translation supplies a fixed generous ceiling.
ANTHROPIC_MAX_TOKENS = 4096

def provider_format(provider) -> str:
    fmt = getattr(provider, "format", None) or "chat_completions"
    return fmt if fmt in FORMATS else "chat_completions"

def _client(timeout):
    # Proxy honoring is driven by the user's setting (系统设置 · 代理):
    #   "none"   -> direct connection, trust_env=False (the default)
    #   "custom" -> the configured http/socks5 proxy address
    #   "system" -> follow OS/environment proxy variables (trust_env=True)
    # trust_env=False in the default path is deliberate: httpx's env-proxy
    # parsing crashes with InvalidURL ("Invalid port: ':1]'") on machines
    # whose NO_PROXY contains bracketed IPv6 entries (reproduced in the dev
    # sandbox), which took down EVERY provider call. "system" mode is the
    # user's explicit opt-in to that behaviour.
    mode = "none"
    proxy_url = ""
    try:
        from app.system.store import get_settings
        settings = get_settings()
        mode = settings.get("proxy_mode") or "none"
        proxy_url = settings.get("proxy_url") or ""
    except Exception:
        pass
    if mode == "custom" and proxy_url:
        return httpx.Client(trust_env=False, timeout=timeout, proxy=proxy_url)
    if mode == "system":
        return httpx.Client(trust_env=True, timeout=timeout)
    return httpx.Client(trust_env=False, timeout=timeout)

def _base(provider) -> str:
    base = getattr(provider, "base_url", None)
    if not base or not isinstance(base, str):
        # Raised as LLMError (not AttributeError) so every caller keeps
        # the module's single error contract even for broken configs.
        raise LLMError("模型供应商还没有填写接口地址")
    return base.rstrip("/")

def _endpoint(provider, suffix: str) -> str:
    """Join base_url + endpoint suffix. A base_url ending in '#' means
    the user pasted the complete endpoint address — use it verbatim
    (minus the marker) instead of appending anything."""
    base = _base(provider)
    if base.endswith("#"):
        return base[:-1]
    return base + suffix

def _bearer_headers(provider):
    h = {"Content-Type": "application/json"}
    if provider.api_key:
        h["Authorization"] = f"Bearer {provider.api_key}"
    return h

def _anthropic_headers(provider):
    h = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
    if provider.api_key:
        h["x-api-key"] = provider.api_key
    return h

def _anthropic_url(provider, suffix: str) -> str:
    # Official base is https://api.anthropic.com (no /v1); proxies often
    # expose the API under a base that already ends in /v1. Accept both.
    base = _base(provider)
    if base.endswith("#"):
        return base[:-1]
    return base + suffix if base.endswith("/v1") else base + "/v1" + suffix

# ---------------------------------------------------------------- OpenAI-style

def _openai_models(provider, timeout):
    url = _endpoint(provider, "/models")
    try:
        with _client(timeout) as c:
            r = c.get(url, headers=_bearer_headers(provider))
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        return [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as e:
        raise LLMError(f"从 {url} 获取模型列表失败：{e}")

def _chat_completions(provider, model_id, messages, timeout):
    url = _endpoint(provider, "/chat/completions")
    try:
        with _client(timeout) as c:
            r = c.post(url, headers=_bearer_headers(provider),
                       json={"model": model_id, "messages": messages})
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        if content is None or (isinstance(content, str) and not content.strip()):
            # Same honesty rule as the responses/anthropic paths: an empty
            # or whitespace-only reply is not a successful answer.
            raise LLMError("模型返回了没有文字内容的结果（可能只包含工具调用）")
        usage = data.get("usage", {}) or {}
        return {"content": content, "token_usage": int(usage.get("total_tokens", 0)),
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0))}
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"调用模型失败，地址 {url}：{e}")

# ------------------------------------------------------------------- Responses

def _responses_payload(model_id, messages):
    """Translate chat messages to a Responses API request body: system
    messages become `instructions`, the rest become input items."""
    instructions = []
    items = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            instructions.append(content if isinstance(content, str) else str(content))
        else:
            items.append({"role": role,
                          "content": [{"type": "input_text",
                                       "text": content if isinstance(content, str) else str(content)}]})
    body = {"model": model_id, "input": items}
    if instructions:
        body["instructions"] = "\n\n".join(instructions)
    return body

def _responses_text(data) -> str:
    parts = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "output_text":
                parts.append(str(block.get("text", "")))
    return "".join(parts)

def _responses(provider, model_id, messages, timeout):
    url = _endpoint(provider, "/responses")
    try:
        with _client(timeout) as c:
            r = c.post(url, headers=_bearer_headers(provider),
                       json=_responses_payload(model_id, messages))
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {}) or {}
        prompt = int(usage.get("input_tokens", 0))
        completion = int(usage.get("output_tokens", 0))
        text = _responses_text(data)
        if not text.strip():
            raise LLMError("模型返回了没有文字内容的结果（可能只包含工具调用或被拒答）")
        return {"content": text,
                "token_usage": int(usage.get("total_tokens", prompt + completion)),
                "prompt_tokens": prompt, "completion_tokens": completion}
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"调用模型失败，地址 {url}：{e}")

# ------------------------------------------------------------------- Anthropic

def _anthropic_models(provider, timeout):
    url = _anthropic_url(provider, "/models")
    try:
        with _client(timeout) as c:
            r = c.get(url, headers=_anthropic_headers(provider))
        r.raise_for_status()
        data = r.json()
        items = data.get("data", [])
        return [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as e:
        raise LLMError(f"从 {url} 获取模型列表失败：{e}")

def _anthropic_payload(model_id, messages):
    """Translate chat messages to a Messages API body: system messages are
    lifted into the top-level `system` field, the rest pass through."""
    system = []
    rest = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system.append(content if isinstance(content, str) else str(content))
        else:
            rest.append({"role": role, "content": content})
    body = {"model": model_id, "max_tokens": ANTHROPIC_MAX_TOKENS, "messages": rest}
    if system:
        body["system"] = "\n\n".join(system)
    return body

def _anthropic(provider, model_id, messages, timeout):
    url = _anthropic_url(provider, "/messages")
    try:
        with _client(timeout) as c:
            r = c.post(url, headers=_anthropic_headers(provider),
                       json=_anthropic_payload(model_id, messages))
        r.raise_for_status()
        data = r.json()
        parts = [str(b.get("text", "")) for b in data.get("content", []) or []
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not "".join(parts).strip():
            raise LLMError(f"模型返回了没有文字内容的结果（结束原因：{data.get('stop_reason') or '未知'}）")
        usage = data.get("usage", {}) or {}
        prompt = int(usage.get("input_tokens", 0))
        completion = int(usage.get("output_tokens", 0))
        return {"content": "".join(parts),
                "token_usage": prompt + completion,
                "prompt_tokens": prompt, "completion_tokens": completion}
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"调用模型失败，地址 {url}：{e}")

# -------------------------------------------------------------------- dispatch

def fetch_models(provider, timeout=20):
    if provider is None:
        raise LLMError("还没有配置模型供应商")
    fmt = provider_format(provider)
    if fmt == "anthropic":
        return _anthropic_models(provider, timeout)
    return _openai_models(provider, timeout)

def chat_completion(provider, model_id, messages, timeout=120):
    if provider is None:
        raise LLMError("这位员工还没有配置模型供应商")
    if not model_id:
        raise LLMError("这位员工还没有选择模型")
    fmt = provider_format(provider)
    if fmt == "anthropic":
        return _anthropic(provider, model_id, messages, timeout)
    if fmt == "responses":
        return _responses(provider, model_id, messages, timeout)
    return _chat_completions(provider, model_id, messages, timeout)
