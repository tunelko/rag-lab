"""Conservative image-input capabilities, independent of names in the UI.

Live input-modality declarations take precedence. Exact-ID fallbacks cover APIs
whose /models response omits modalities; never infer vision from a family prefix.
References and update procedure: docs/CLOUD_VISION.md.
"""
import base64
import binascii


DOCUMENTED = {
    'groq': {'qwen/qwen3.8-27b': 3, 'meta-models/muse-spark-open': 4},
    'scaleway': {name: 4 for name in (
        'gemma-3-27b-it', 'gemma-4-26b-a4b-it', 'gemma-4-31b-it',
        'qwen3.8-27b', 'qwen3.6-35b-a3b',
        'qwen3.5-397b-a17b', 'qwen3.5-122b-a10b', 'qwen3.5-35b-a3b',
        'mistral-medium-3.5-128b', 'mistral-small-3.2-24b-instruct-2506',
        'mistral-large-3-675b-instruct-2512', 'deepseek-v4.1-flash-1017',
    )},
    'ovh': {name: 4 for name in (
        'Qwen3.8-27B', 'Qwen2.5-VL-72B-Instruct', 'Mistral-Small-3.2-24B-Instruct-2506',
        'Qwen3.5-397B-A17B', 'Qwen3.6-27B', 'Qwen3.5-9B',
    )},
    'xai': {'grok-4': 4, 'grok-4.7': 4},
}
# Only populated by authenticated provider catalog responses, never browser input.
_discovered = {}


def register(provider, item):
    name = item['id']
    _discovered.pop((provider, name), None)
    modalities = item.get('input_modalities')
    if modalities is None and isinstance(item.get('architecture'), dict):
        modalities = item['architecture'].get('input_modalities')
    if modalities is None and isinstance(item.get('modalities'), dict):
        modalities = item['modalities'].get('input')
    if isinstance(modalities, list):
        _discovered[provider, name] = 4 if 'image' in modalities else 0


def image_limit(provider, model):
    documented = DOCUMENTED.get(provider, {}).get(model, 0)
    discovered = _discovered.get((provider, model))
    return min(documented or 4, discovered) if discovered is not None else documented


def image_parts(images):
    """Only embedded, already normalized PNG/JPEG bytes, never remote URLs."""
    result = []
    for encoded in images:
        try:
            if not isinstance(encoded, str) or len(encoded) > 12 * 1024**2:
                raise ValueError('Imagen demasiado grande.')
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('La imagen no contiene datos base64 válidos.') from exc
        mime = 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
        if not mime:
            raise ValueError('El proveedor visual requiere imágenes PNG o JPEG normalizadas.')
        result.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}})
    return result
