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
    "groq": Provider("groq", "Groq", "https://api.groq.com/openai/v1", "api.groq.com"),
}

# Identify this API client instead of using urllib's generic default, which some
# provider edges reject. Product name only: no URL or anything naming the installation.
CLIENT_USER_AGENT = "RAG-Local/1.0"


def _groq_chat_model(identifier):
    """The live /models catalog also includes audio, classifiers and hosted agents."""
    return not identifier.casefold().startswith((
        "whisper-", "distil-whisper-", "playai-tts", "canopylabs/orpheus-",
        "meta-llama/llama-prompt-guard-", "meta-llama/llama-guard-",
        "openai/gpt-oss-safeguard-", "groq/compound", "compound-beta",
    ))


def _usage(result, provider):
    usage = result.get("usage")
    if not usage and provider == "groq":
        usage = (result.get("x_groq") or {}).get("usage")
    return usage if isinstance(usage, dict) else {}


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


def _rejection_message(profile: Provider, code: int, detail: str, action: str):
    if profile.id == "scaleway" and code == 403:
        return (
            "Scaleway autenticó la Secret Key, pero su usuario o aplicación no tiene permiso para "
            f"{action}. Asigna GenerativeApisModelAccess (mínimo recomendado), "
            "GenerativeApisFullAccess o AllProductsFullAccess en IAM y verifica que la política "
            "abarque el proyecto u organización correctos."
        )
    return f"{profile.label} rechazó {action} ({code}): {detail}"


def _json_schema(schema):
    return {"type": "json_schema", "json_schema": {"name": "rag_global_answer", "strict": True, "schema": schema}}


def list_models(provider: str, api_key: str, *, timeout=30, opener=None):
    """Return model identifiers without ever returning or logging the credential."""
    profile = _profile(provider)
    request = urllib.request.Request(
        profile.base_url + "/models",
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json", "User-Agent": CLIENT_USER_AGENT},
        method="GET",
    )
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with client.open(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096).decode("utf-8", "replace"))
            detail = detail.get("error", detail)
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("detail") or detail.get("title") or str(detail)
        except (ValueError, OSError):
            detail = exc.reason
        clean_detail = str(detail).replace(api_key, "[credencial omitida]")
        raise ProviderError(_rejection_message(profile, exc.code, clean_detail, "la consulta de modelos")) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"No se pudo consultar el catálogo de {profile.label}: {exc.reason}") from exc
    except (ValueError, TypeError) as exc:
        raise ProviderError(f"{profile.label} devolvió un catálogo de modelos no válido.") from exc
    items = result.get("data", []) if isinstance(result, dict) else []
    models = set()
    for item in items:
        identifier = item.get("id") if isinstance(item, dict) else None
        if (isinstance(identifier, str) and 1 <= len(identifier) <= 300
                and not any(character in identifier for character in "\r\n\0")):
            if provider == "groq" and (item.get("active") is False or not _groq_chat_model(identifier)):
                continue
            models.add(identifier)
    return sorted(models, key=str.casefold)


def generate(messages, provider: str, model: str, api_key: str, *, on_chunk=None, cancelled=None,
             max_output_tokens=None, response_format=None, timeout=600, opener=None, context_length=None):
    del context_length  # The remote model owns its context window.
    if cancelled and cancelled():
        raise InterruptedError("Generación cancelada antes de contactar con el proveedor externo.")
    profile = _profile(provider)
    model = model.strip()
    if not model or len(model) > 300:
        raise ProviderError("Configura un nombre de modelo externo válido.")
    if provider == "groq" and not _groq_chat_model(model):
        raise ProviderError("Selecciona un modelo de chat de Groq; audio, clasificación y Compound no se usan para responder al RAG.")
    limit = 512 if max_output_tokens is None else int(max_output_tokens)
    if not 1 <= limit <= 4096:
        raise ProviderError("El límite de salida debe estar entre 1 y 4096 tokens.")
    body = {
        "model": model,
        "messages": _messages(messages),
        "temperature": 0.1,
        "max_completion_tokens" if provider == "groq" else "max_tokens": limit,
        "stream": on_chunk is not None,
    }
    if on_chunk is not None:
        body["stream_options"] = {"include_usage": True}
    if response_format is not None:
        if provider == "groq":
            # JSON object mode supports streaming across Groq chat models. Strict
            # schema mode does not; GlobalAnswer still validates every document.
            body["response_format"] = {"type": "json_object"}
            body["messages"].insert(0, {"role": "system", "content":
                "Return only a JSON object matching this schema: " + json.dumps(response_format, ensure_ascii=False)})
        else:
            body["response_format"] = _json_schema(response_format)
    request = urllib.request.Request(
        profile.base_url + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "text/event-stream" if on_chunk else "application/json", "User-Agent": CLIENT_USER_AGENT},
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
                usage = _usage(result, provider)
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
                    error = item.get("error") or ((item.get("x_groq") or {}).get("error") if provider == "groq" else None)
                    if error:
                        detail = error.get("message", "El proveedor devolvió un error.") if isinstance(error, dict) else str(error)
                        raise ProviderError(detail.replace(api_key, "[credencial omitida]"))
                    usage = _usage(item, provider) or usage
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
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("detail") or detail.get("title") or str(detail)
        except (ValueError, OSError):
            detail = exc.reason
        detail = str(detail).replace(api_key, "[credencial omitida]")
        raise ProviderError(_rejection_message(profile, exc.code, detail, "realizar la petición")) from exc
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
