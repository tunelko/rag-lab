"""Response-generation adapters. Document ingestion and vision stay on local Ollama."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    host: str


PROVIDERS = {
    "scaleway": Provider("scaleway", "Scaleway", "https://api.scaleway.ai/v1", "api.scaleway.ai"),
    "ovh": Provider("ovh", "OVHcloud", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "oai.endpoints.kepler.ai.cloud.ovh.net"),
    "xai": Provider("xai", "xAI · Grok", "https://api.x.ai/v1", "api.x.ai"),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("El proveedor intentó redirigir la petición; no se reenviará el contexto ni la credencial.")


def provider_info():
    return [{"id": "ollama", "label": "Ollama local", "cloud": False}, *(
        {"id": item.id, "label": item.label, "cloud": True} for item in PROVIDERS.values()
    )]


def _profile(provider: str):
    try:
        profile = PROVIDERS[provider]
    except KeyError as exc:
        raise ProviderError("Proveedor de generación no válido.") from exc
    parsed = urlsplit(profile.base_url)
    if parsed.scheme != "https" or parsed.hostname != profile.host or parsed.username or parsed.password:
        raise ProviderError("Endpoint externo no permitido.")
    return profile


def _content(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(item.get("text", "") for item in value if isinstance(item, dict) and item.get("type") in {"text", "output_text"})
    return str(value)


def _messages(messages):
    clean = []
    for message in messages:
        if message.get("images"):
            raise ProviderError("Las imágenes se procesan solo con Ollama local; no se enviarán a un proveedor externo.")
        clean.append({"role": message["role"], "content": _content(message.get("content"))})
    return clean


def _json_schema(schema):
    return {"type": "json_schema", "json_schema": {"name": "rag_global_answer", "strict": True, "schema": schema}}


def generate(messages, provider: str, model: str, api_key: str, *, on_chunk=None, cancelled=None,
             max_output_tokens=None, response_format=None, timeout=600, opener=None, context_length=None):
    del context_length  # The remote model owns its context window.
    if cancelled and cancelled():
        raise InterruptedError("Generación cancelada antes de contactar con el proveedor externo.")
    profile = _profile(provider)
    model = model.strip()
    if not model or len(model) > 300:
        raise ProviderError("Configura un nombre de modelo externo válido.")
    limit = 512 if max_output_tokens is None else int(max_output_tokens)
    if not 1 <= limit <= 4096:
        raise ProviderError("El límite de salida debe estar entre 1 y 4096 tokens.")
    body = {
        "model": model,
        "messages": _messages(messages),
        "temperature": 0.1,
        "max_tokens": limit,
        "stream": on_chunk is not None,
    }
    if on_chunk is not None:
        body["stream_options"] = {"include_usage": True}
    if response_format is not None:
        body["response_format"] = _json_schema(response_format)
    request = urllib.request.Request(
        profile.base_url + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "text/event-stream" if on_chunk else "application/json"},
        method="POST",
    )
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    started = time.perf_counter()
    first_content = None
    pieces, usage, finish_reason, completed = [], {}, None, on_chunk is None
    try:
        response = client.open(request, timeout=timeout)
        with response:
            if on_chunk is None:
                result = json.load(response)
                choice = (result.get("choices") or [{}])[0]
                answer = _content(choice.get("message", {}).get("content"))
                usage = result.get("usage") or {}
                finish_reason = choice.get("finish_reason")
            else:
                for raw in response:
                    if cancelled and cancelled():
                        raise InterruptedError("Generación cancelada.")
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        completed = True
                        break
                    item = json.loads(data)
                    if item.get("error"):
                        error = item["error"]
                        detail = error.get("message", "El proveedor devolvió un error.") if isinstance(error, dict) else str(error)
                        raise ProviderError(detail.replace(api_key, "[credencial omitida]"))
                    if item.get("usage"):
                        usage = item["usage"]
                    choice = (item.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    piece = _content(choice.get("delta", {}).get("content"))
                    if piece:
                        if first_content is None:
                            first_content = time.perf_counter() - started
                        pieces.append(piece)
                        on_chunk(piece)
                answer = "".join(pieces)
                if not completed:
                    raise ProviderError("El proveedor cerró el stream sin confirmar que la respuesta había terminado.")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096).decode("utf-8", "replace"))
            detail = detail.get("error", detail)
            detail = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        except (ValueError, OSError):
            detail = exc.reason
        detail = str(detail).replace(api_key, "[credencial omitida]")
        raise ProviderError(f"{profile.label} rechazó la petición ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"No se pudo conectar con {profile.label}: {exc.reason}") from exc
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = time.perf_counter() - started
    generation_seconds = total - first_content if first_content is not None else None
    return answer, {
        "backend": "openai-compatible",
        "provider": provider,
        "provider_label": profile.label,
        "cloud": True,
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens") or prompt_tokens + output_tokens),
        "total_seconds": round(total, 2),
        "first_content_seconds": round(first_content, 2) if first_content is not None else None,
        "generation_tok_s": round(output_tokens / generation_seconds, 2) if output_tokens and generation_seconds and generation_seconds > 0 else None,
        "done_reason": finish_reason,
        "thinking_characters": 0,
    }
