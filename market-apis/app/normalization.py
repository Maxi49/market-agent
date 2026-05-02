import re

from app.models import NormalizedProduct, Product, ProductCondition, QueryUnderstanding
from app.ranking import ACCESSORY_TERMS
from app.routing import normalize_text, understand_query
from app.scrapers.base import detect_condition

# Tokens that appear at the start of product titles but are NOT brand names.
# Used by the positional brand heuristic to skip past category/adjective words.
_TITLE_NON_BRAND_TOKENS = {
    # categories
    "celular", "smartphone", "telefono", "tablet", "notebook", "laptop",
    "computadora", "pc", "smart", "tv", "televisor", "monitor", "pantalla",
    "heladera", "lavarropas", "microondas", "freezer", "aire", "acondicionado",
    "auriculares", "audifonos", "parlante", "bocina", "teclado", "mouse",
    "impresora", "router", "camara", "reloj",
    # adjectives / conditions
    "nuevo", "nueva", "original", "libre", "desbloqueado", "sellado",
    "reacondicionado", "usado", "refurbished",
    # connectors
    "con", "sin", "de", "el", "la", "los", "las", "para", "y", "a",
    # units / specs
    "pulgadas", "pulg", "generacion", "gen", "ram",
}

COLOR_TERMS = {
    "amarillo",
    "azul",
    "blanco",
    "black",
    "blue",
    "gris",
    "negro",
    "red",
    "rojo",
    "verde",
    "white",
}

# Storage is always >= 32GB; smaller values are RAM (4/8/12/16GB).
# When a title has multiple GB values (e.g. "512GB 12GB RAM"), we want the storage one.
_RAM_MAX_GB = 24


class ProductNormalizer:
    def normalize(
        self,
        product: Product,
        query_understanding: QueryUnderstanding | None = None,
    ) -> NormalizedProduct:
        title = normalize_text(product.title)
        inferred = understand_query(product.title)
        structured = _structured_metadata(product)
        brand = (
            _structured_text(structured.get("brand"))
            or _metadata_brand(product.raw_metadata)
            or _first(inferred.detected_brands)
            or _first(query_understanding.detected_brands if query_understanding else [])
            or _infer_brand_from_title(product.title)
        )
        category = (
            _structured_category(structured.get("category"))
            or inferred.detected_category
            or (query_understanding.detected_category if query_understanding else None)
        )
        model = _structured_text(structured.get("model")) or _extract_model(title, brand)
        attributes = {
            **(query_understanding.attributes if query_understanding else {}),
            **inferred.attributes,
        }
        # Prefer storage extracted from full title (more context) over query-level
        title_storage = _extract_storage(title)
        if title_storage:
            attributes["storage"] = title_storage
        screen_size = _extract_screen_size(title)
        if screen_size:
            attributes["screen_size"] = screen_size
        ram = _extract_ram(title)
        if ram:
            attributes["ram"] = ram
        cpu = _extract_cpu(title)
        if cpu:
            attributes["cpu"] = cpu
        gpu = _extract_gpu(title)
        if gpu:
            attributes["gpu"] = gpu
        color = _extract_color(title)
        if color:
            attributes["color"] = color
        is_bundle = _is_bundle_title(title)
        if is_bundle:
            attributes["bundle"] = "true"

        condition = product.condition
        if condition == ProductCondition.UNKNOWN:
            condition = detect_condition(product.title, " ".join(str(value) for value in product.raw_metadata.values()))

        is_accessory = bool(set(title.split()) & ACCESSORY_TERMS)
        normalized_title = _normalized_title(brand, model, attributes, product.title)
        canonical_key = _canonical_key(brand, model, attributes, normalized_title)

        return NormalizedProduct(
            canonical_key=canonical_key,
            normalized_title=normalized_title,
            brand=brand,
            model=model,
            category=category,
            attributes=attributes,
            is_accessory=is_accessory,
            condition=condition,
            raw_compact={
                "store_id": product.store_id,
                "position": product.position,
                "title": product.title,
                "price": product.price,
                "url": str(product.product_url),
                "metadata": product.raw_metadata,
                "structured": structured,
                "is_bundle": is_bundle,
                "extracted_attributes": attributes,
            },
        )


def _extract_model(title: str, brand: str | None) -> str | None:
    if brand == "apple":
        iphone = re.search(r"\biphone\s+\d{1,2}(?:\s+(?:pro|max|plus))*\b", title)
        if iphone:
            return iphone.group(0)
        # Handle "macbook air/pro" even when "apple" appears between words
        for model_name in ("macbook air", "macbook pro"):
            if re.search(r"\b" + model_name.replace(" ", r"\b.*\b") + r"\b", title):
                return model_name
        for model_name in ("macbook", "ipad", "airpods"):
            if model_name in title:
                return model_name
    if brand == "samsung":
        # Galaxy phones: S24, S24 Ultra, A55, etc. — include "+" suffix
        galaxy = re.search(r"\bgalaxy\s+[a-z]?\d{1,3}\+?(?:\s+(?:fe|ultra|plus))*\b", title)
        if galaxy:
            return galaxy.group(0)
        # Samsung Galaxy Buds — normalize "buds4" and "buds 4" to same form
        buds = re.search(r"\bgalaxy\s+buds\s*(\d+|\+)?\s*(?:pro|live|fe)?\b", title)
        if buds:
            # normalize: "galaxy buds 4 pro" and "galaxy buds4 pro" → "galaxy buds 4 pro"
            raw = buds.group(0).strip()
            return re.sub(r"buds(\d)", r"buds \1", raw)
        # Samsung TV model number (e.g. UN55U8000F, QN55Q70C, 55DU7000)
        tv_model = re.search(r"\b(?:un|qn|q|s)?\d{2}[a-z]{1,3}\d{3,5}[a-z]?\b", title)
        if tv_model:
            return tv_model.group(0)
        if "smart tv" in title or "televisor" in title:
            return "smart tv"
    if brand == "motorola":
        # Moto Edge 50 Pro, Moto G55, etc.
        moto = re.search(r"\b(?:moto(?:rola)?\s+)?(?:edge|g|e)\s*\d{1,3}(?:\s+(?:pro|plus|ultra|play|power))?\b", title)
        if moto:
            result = moto.group(0).strip()
            result = re.sub(r"^moto(?:rola)?\s+", "", result)
            return result
    # Generic: extract an alphanumeric product code (e.g. PHLF6510P2, 43S5K, 55C450NS)
    # Requires at least 2 letters and 3 digits together (avoids matching RAM/storage specs)
    if brand:
        code = re.search(r"\b(?=[a-z]*\d)(?=[0-9]*[a-z])[a-z0-9]{5,12}\b", title)
        if code and not re.fullmatch(r"\d+(?:gb|tb|ssd|ram)", code.group(0)):
            return code.group(0)
    return None


def _extract_storage(title: str) -> str | None:
    # Match "256GB", "512 gb", "1TB", and also "256ssd" / "512ssd" (common in laptop listings)
    matches = re.findall(r"\b(\d{1,4})\s*(?:(gb|tb)|ssd)\b", title)
    if not matches:
        return None
    storage_candidates = [
        (int(amount), unit or "gb")
        for amount, unit in matches
        if (unit == "tb") or int(amount) > _RAM_MAX_GB
    ]
    if not storage_candidates:
        return None
    # Pick the largest value — most specific storage spec wins
    amount, unit = max(storage_candidates, key=lambda x: x[0] * (1024 if x[1] == "tb" else 1))
    return f"{amount}{unit.upper()}"


def _extract_screen_size(title: str) -> str | None:
    match = re.search(r"\b(\d{2}(?:[.,]\d)?)\s*(?:\"|pulgadas|pulg|inch|in)\b", title)
    if not match:
        return None
    amount = match.group(1).replace(",", ".")
    if amount.endswith(".0"):
        amount = amount[:-2]
    return f'{amount}"'


def _extract_ram(title: str) -> str | None:
    explicit_patterns = [
        r"\b(\d{1,3})\s*gb\s*(?:de\s*)?ram\b",
        r"\bram\s*(\d{1,3})\s*gb\b",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, title)
        if match:
            amount = int(match.group(1))
            if amount <= _RAM_MAX_GB:
                return f"{amount}GB"

    values = [int(amount) for amount in re.findall(r"\b(\d{1,3})\s*gb\b", title)]
    ram_candidates = [value for value in values if value <= _RAM_MAX_GB]
    if not ram_candidates:
        return None
    return f"{max(ram_candidates)}GB"


def _extract_cpu(title: str) -> str | None:
    intel = re.search(r"\b(?:intel\s+core\s+)?i([3579])(?:[-\s]?\d{3,5}[a-z]*)?\b", title)
    if intel:
        return f"i{intel.group(1)}"
    ryzen = re.search(r"\bryzen\s*([3579])(?:\s+\d{3,5}[a-z]*)?\b", title)
    if ryzen:
        return f"ryzen {ryzen.group(1)}"
    return None


def _extract_gpu(title: str) -> str | None:
    gpu = re.search(r"\b(?:nvidia\s+|geforce\s+)?(rtx|gtx)\s*(\d{3,4})\b", title)
    if not gpu:
        return None
    return f"{gpu.group(1)} {gpu.group(2)}"


def _extract_color(title: str) -> str | None:
    tokens = set(title.split())
    matches = sorted(tokens & COLOR_TERMS)
    return matches[0] if matches else None


def _is_bundle_title(title: str) -> bool:
    if re.search(r"\b(combo|pack|kit|bundle)\b", title):
        return True
    if "+" in title and bool(set(title.split()) & ACCESSORY_TERMS):
        return True
    return False


def _normalized_title(
    brand: str | None,
    model: str | None,
    attributes: dict[str, str],
    fallback: str,
) -> str:
    parts = []
    if brand:
        parts.append(brand.title())
    if model:
        parts.append(model.title())
    if "storage" in attributes:
        parts.append(attributes["storage"])
    if not parts:
        return fallback
    return " ".join(parts)


def _canonical_key(
    brand: str | None,
    model: str | None,
    attributes: dict[str, str],
    normalized_title: str,
) -> str:
    if brand or model:
        parts = [p for p in [brand or "", model or ""] if p]
        for key in ["storage", "screen_size"]:
            if key in attributes:
                parts.append(attributes[key])
        return normalize_text(" ".join(parts)).replace(" ", "-")
    return normalize_text(normalized_title).replace(" ", "-")


def _infer_brand_from_title(title: str) -> str | None:
    """Return the first token that looks like a brand name.

    Scans the first 5 tokens of the original title (case-preserved), skips
    known category/adjective words, and returns the first candidate that is
    alphabetic and long enough to be a real brand.
    """
    normalized = normalize_text(title)
    norm_tokens = normalized.split()[:5]
    for token in norm_tokens:
        clean = re.sub(r"[^a-z0-9]", "", token)
        if not clean or len(clean) < 2:
            continue
        if clean in _TITLE_NON_BRAND_TOKENS:
            continue
        if clean.isdigit():
            continue
        return clean
    return None


def _structured_metadata(product: Product) -> dict[str, str]:
    structured = product.raw_metadata.get("structured")
    if not isinstance(structured, dict):
        return {}
    return {
        str(key): str(value).strip()
        for key, value in structured.items()
        if value is not None and str(value).strip()
    }


def _structured_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    return normalized or None


def _structured_category(value: str | None) -> str | None:
    normalized = _structured_text(value)
    return normalized.replace(" ", "_") if normalized else None


def _metadata_brand(raw_metadata: dict) -> str | None:
    brand = raw_metadata.get("brand")
    return normalize_text(str(brand)) if brand else None


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None
