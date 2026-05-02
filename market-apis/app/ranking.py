import re
import unicodedata

from app.models import Product, ProductCondition

ACCESSORY_TERMS = {
    "adaptador",
    "cable",
    "carcasa",
    "case",
    "cargador",
    "funda",
    "hybrid",
    "lamina",
    "magfit",
    "pen",
    "protector",
    "proteccion",
    "repuesto",
    "s-pen",
    "soporte",
    "spigen",
    "templado",
    "vidrio",
}
STOPWORDS = {
    "con",
    "de",
    "del",
    "el",
    "en",
    "la",
    "las",
    "los",
    "para",
    "por",
    "un",
    "una",
}


def rank_products(query: str, products: list[Product], limit: int) -> list[Product]:
    relevant_products = [product for product in products if is_relevant(query, product)]
    return sorted(relevant_products, key=lambda product: _ranking_key(query, product))[:limit]


def is_relevant(query: str, product: Product) -> bool:
    query_tokens = _tokens(query)
    title_tokens = _tokens(product.title)
    if not query_tokens:
        return True

    if ACCESSORY_TERMS.isdisjoint(query_tokens) and not ACCESSORY_TERMS.isdisjoint(title_tokens):
        return False

    overlap = len(query_tokens & title_tokens)
    min_overlap = 1 if len(query_tokens) <= 2 else 2
    return overlap >= min_overlap


def _ranking_key(query: str, product: Product) -> tuple[int, bool, float, int, int]:
    condition_penalty = _condition_penalty(query, product)
    price_missing = product.price is None
    price = product.price if product.price is not None else float("inf")
    sponsored_penalty = 1 if product.sponsored else 0
    return (condition_penalty, price_missing, price, sponsored_penalty, product.position)


def _condition_penalty(query: str, product: Product) -> int:
    query_tokens = _tokens(query)
    accepts_non_new = bool({"usado", "usada", "reacondicionado", "reacondicionada"} & query_tokens)
    if accepts_non_new:
        return 0
    if product.condition in {ProductCondition.USED, ProductCondition.REFURBISHED}:
        return 1
    return 0


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return {
        token
        for token in re.findall(r"[a-z0-9]+", ascii_text)
        if token not in STOPWORDS and len(token) > 1
    }
