"""Live public provider tariffs. No credentials, prompts or fixed price tables."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import time
import urllib.error
import urllib.request

from generation_providers import _NoRedirect


SCALEWAY_CATALOG = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
SCALEWAY_SOURCE = "https://www.scaleway.com/en/developers/api/product-catalog/public-catalog"


class PricingError(ValueError):
    pass


def _rate(product):
    money = (product.get("price") or {}).get("retail_price")
    measure = product.get("unit_of_measure")
    if not isinstance(money, dict) or not isinstance(measure, dict):
        return None
    currency = money.get("currency_code", "")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return None
    try:
        units = Decimal(str(money["units"]))
        nanos = Decimal(str(money["nanos"]))
        size = Decimal(str(measure["size"]))
        if not all(value.is_finite() for value in (units, nanos, size)):
            return None
        if units < 0 or nanos < 0 or nanos >= 10**9 or size <= 0:
            return None
        scale = {"token": (1000000, "million_tokens"), "second": (60, "audio_minute")}.get(measure["unit"])
        if scale is None:
            return None
        amount = (units + nanos / Decimal(10**9)) * scale[0] / size
        return {"amount": str(amount), "currency": currency, "unit": scale[1]}
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


def fetch_pricing(provider, *, opener=None, timeout=20):
    """Fetch current realtime Paris retail rates, reading all catalog pages."""
    if provider != "scaleway":
        return {"provider": provider, "supported": False, "models": {}}
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    models, seen, conflicts = {}, set(), set()
    started, count = time.monotonic(), 0
    try:
        for page in range(1, 21):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise PricingError("La consulta de tarifas de Scaleway agotó el tiempo de espera.")
            url = f"{SCALEWAY_CATALOG}?product_types=generative_apis&region=fr-par&page_size=100&page={page}"
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with client.open(request, timeout=remaining) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise PricingError("El catálogo de tarifas supera el límite de respuesta.")
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("products"), list):
                raise PricingError("Scaleway devolvió un catálogo de tarifas no válido.")
            products = result["products"]
            total = int(result["total_count"])
            if total < 0 or total > 2000 or (not products and count < total):
                raise PricingError("El catálogo de tarifas está incompleto o supera el límite de paginación.")
            count += len(products)
            for product in products:
                if not isinstance(product, dict):
                    continue
                sku = product.get("sku")
                if not sku or sku in seen:
                    continue
                seen.add(sku)
                props = (product.get("properties") or {}).get("generative_apis") or {}
                if props.get("consumption_mode") != "realtime" or (product.get("locality") or {}).get("region") != "fr-par":
                    continue
                identifier = product.get("product")
                kind = {"input_token": "input", "output_token": "output",
                        "input_cached_token": "cached_input", "input_audio": "input"}.get(props.get("token_type"))
                rate = _rate(product)
                if not isinstance(identifier, str) or not kind or rate is None:
                    continue
                entry = models.setdefault(identifier, {})
                key = (identifier, kind)
                if kind in entry and entry[kind] != rate:
                    conflicts.add(key)
                entry[kind] = rate
            if count >= total:
                break
        else:
            raise PricingError("No se pudo completar la paginación de tarifas de Scaleway.")
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, PricingError):
            raise
        raise PricingError("No se pudieron consultar las tarifas actuales de Scaleway. Reintenta Actualizar modelos.") from exc
    for model, kind in conflicts:
        models[model].pop(kind, None)
    return {"provider": provider, "supported": True, "models": models, "mode": "realtime",
            "region": "fr-par", "tax_included": False, "source_url": SCALEWAY_SOURCE,
            "retrieved_at": datetime.now(timezone.utc).isoformat()}
