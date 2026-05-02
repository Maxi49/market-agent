import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from app.models import QueryUnderstanding, RoutingDecision


STORE_IDS = [
    "mercado_libre", "fravega", "samsung_ar", "carrefour_ar", "cetrogar_ar",
    "easy_ar", "bgh_ar", "sony_ar", "amazon_us",
]

BRAND_KEYWORDS = {
    "apple": {"apple", "iphone", "ipad", "macbook", "airpods"},
    "samsung": {"samsung", "galaxy"},
    "motorola": {"motorola", "moto"},
    "lg": {"lg"},
    "sony": {"sony", "bravia"},
    "tcl": {"tcl"},
    "philco": {"philco"},
    "noblex": {"noblex"},
    "hisense": {"hisense"},
    "xiaomi": {"xiaomi", "redmi", "poco"},
    "lenovo": {"lenovo", "thinkpad", "ideapad"},
    "hp": {"hp", "hewlett"},
    "dell": {"dell"},
    "asus": {"asus"},
    "acer": {"acer"},
    "bgh": {"bgh"},
    "amazon": {"amazon", "kindle", "echo", "fire"},
}

CATEGORY_KEYWORDS = {
    "smartphones": {"celular", "smartphone", "iphone", "galaxy", "motorola", "moto"},
    "notebooks": {"notebook", "laptop", "macbook"},
    "audio": {"auricular", "auriculares", "headphone", "headphones", "earbuds", "airpods"},
    "gaming": {"gaming", "playstation", "xbox", "nintendo", "steam", "joystick"},
    "accessories": {"funda", "case", "cargador", "charger", "cable", "adapter", "adaptador"},
    "books": {"libro", "book", "kindle", "paperwhite", "ebook"},
    "tv": {"tv", "televisor", "smart"},
    "home_appliances": {"heladera", "lavarropas", "microondas", "freezer", "aire"},
    "supermarket": {"supermercado", "yerba", "leche", "arroz", "fideos", "aceite"},
}

ACCESSORY_INTENT_KEYWORDS = {
    "adaptador",
    "adapter",
    "cargador",
    "charger",
    "cable",
    "case",
    "funda",
    "protector",
    "repuesto",
    "soporte",
}


class StoreFit(StrEnum):
    STRONG = "strong"
    OK = "ok"
    WEAK = "weak"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class StoreProfile:
    store_id: str
    default_fit: StoreFit
    category_fit: dict[str, StoreFit]
    brand_fit: dict[str, StoreFit]


STORE_PROFILES = {
    "mercado_libre": StoreProfile(
        store_id="mercado_libre",
        default_fit=StoreFit.STRONG,
        category_fit={
            "smartphones": StoreFit.STRONG,
            "notebooks": StoreFit.STRONG,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.OK,
        },
        brand_fit={},
    ),
    "fravega": StoreProfile(
        store_id="fravega",
        default_fit=StoreFit.OK,
        category_fit={
            "smartphones": StoreFit.STRONG,
            "notebooks": StoreFit.STRONG,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={},
    ),
    "samsung_ar": StoreProfile(
        store_id="samsung_ar",
        default_fit=StoreFit.WEAK,
        category_fit={
            "smartphones": StoreFit.OK,
            "notebooks": StoreFit.BLOCKED,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={"samsung": StoreFit.STRONG, "apple": StoreFit.BLOCKED},
    ),
    "carrefour_ar": StoreProfile(
        store_id="carrefour_ar",
        default_fit=StoreFit.WEAK,
        category_fit={
            "smartphones": StoreFit.WEAK,
            "notebooks": StoreFit.WEAK,
            "tv": StoreFit.OK,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.STRONG,
        },
        brand_fit={"apple": StoreFit.BLOCKED},
    ),
    "cetrogar_ar": StoreProfile(
        store_id="cetrogar_ar",
        default_fit=StoreFit.OK,
        category_fit={
            "smartphones": StoreFit.STRONG,
            "notebooks": StoreFit.STRONG,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={},
    ),
    # Easy: hogar/construcción/electrodomésticos (Cencosud). Fuerte en electro y TV,
    # no es su core smartphones/notebooks pero los tiene.
    "easy_ar": StoreProfile(
        store_id="easy_ar",
        default_fit=StoreFit.OK,
        category_fit={
            "smartphones": StoreFit.WEAK,
            "notebooks": StoreFit.WEAK,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.WEAK,
        },
        brand_fit={},
    ),
    # BGH: marca argentina de electrodomésticos y electrónica.
    # Fuerte en heladeras, lavarropas, AC, microondas, TVs. No vende PCs ni iPhones.
    "bgh_ar": StoreProfile(
        store_id="bgh_ar",
        default_fit=StoreFit.WEAK,
        category_fit={
            "smartphones": StoreFit.WEAK,
            "notebooks": StoreFit.BLOCKED,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={"bgh": StoreFit.STRONG, "apple": StoreFit.BLOCKED},
    ),
    # Megatone: electrodomésticos, TVs, celulares, audio, climatización. Similar a Fravega.
    "megatone_ar": StoreProfile(
        store_id="megatone_ar",
        default_fit=StoreFit.OK,
        category_fit={
            "smartphones": StoreFit.OK,
            "notebooks": StoreFit.OK,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.STRONG,
            "supermarket": StoreFit.BLOCKED,
            "gaming": StoreFit.WEAK,
            "books": StoreFit.BLOCKED,
        },
        brand_fit={},
    ),
    # Sony Store: solo productos Sony. Bloqueado para todo lo que no sea Sony.
    "sony_ar": StoreProfile(
        store_id="sony_ar",
        default_fit=StoreFit.BLOCKED,
        category_fit={
            "smartphones": StoreFit.WEAK,
            "notebooks": StoreFit.BLOCKED,
            "tv": StoreFit.STRONG,
            "home_appliances": StoreFit.WEAK,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={"sony": StoreFit.STRONG, "apple": StoreFit.BLOCKED, "samsung": StoreFit.BLOCKED},
    ),
    # Amazon US es una referencia internacional opcional. No reemplaza tiendas
    # locales porque precio final, impuestos, garantia y envio pueden variar.
    "amazon_us": StoreProfile(
        store_id="amazon_us",
        default_fit=StoreFit.OK,
        category_fit={
            "smartphones": StoreFit.STRONG,
            "notebooks": StoreFit.STRONG,
            "audio": StoreFit.STRONG,
            "gaming": StoreFit.STRONG,
            "accessories": StoreFit.STRONG,
            "books": StoreFit.STRONG,
            "tv": StoreFit.OK,
            "home_appliances": StoreFit.WEAK,
            "supermarket": StoreFit.BLOCKED,
        },
        brand_fit={"amazon": StoreFit.STRONG},
    ),
}


class StoreRouter:
    def __init__(self, available_store_ids: list[str]) -> None:
        self.available_store_ids = available_store_ids

    def route(self, query: str) -> RoutingDecision:
        understanding = understand_query(query)
        selected: list[str] = []
        excluded: list[str] = []
        reasons: dict[str, str] = {}

        for store_id in self.available_store_ids:
            fit = _store_fit(store_id, understanding)
            if fit == StoreFit.BLOCKED:
                excluded.append(store_id)
                reasons[store_id] = "blocked_by_store_profile"
            elif fit == StoreFit.WEAK:
                excluded.append(store_id)
                reasons[store_id] = "deferred_weak_profile"
            else:
                selected.append(store_id)
                reasons[store_id] = f"selected_by_{fit.value}_profile"

        return RoutingDecision(
            selected_store_ids=selected,
            excluded_store_ids=excluded,
            reasons=reasons,
            query_understanding=understanding,
        )


def understand_query(query: str) -> QueryUnderstanding:
    normalized = normalize_text(query)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))

    brands = [
        brand
        for brand, keywords in BRAND_KEYWORDS.items()
        if tokens & keywords
    ]

    category = None
    if tokens & ACCESSORY_INTENT_KEYWORDS:
        category = "accessories"
    else:
        for candidate, keywords in CATEGORY_KEYWORDS.items():
            if tokens & keywords:
                category = candidate
                break

    attributes: dict[str, str] = {}
    storage = re.search(r"\b(\d{2,4})\s*(gb|tb)\b", normalized)
    if storage:
        attributes["storage"] = f"{storage.group(1)}{storage.group(2).upper()}"
    size = re.search(r"\b(\d{2})\s*(?:pulgadas|pulg|\"|inch|inches)?\b", normalized)
    if size and category == "tv":
        attributes["screen_size"] = f"{size.group(1)}\""

    return QueryUnderstanding(
        original_query=query,
        normalized_query=normalized,
        detected_brands=brands,
        detected_category=category,
        attributes=attributes,
    )


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_text).strip()


def _store_fit(store_id: str, understanding: QueryUnderstanding) -> StoreFit:
    profile = STORE_PROFILES.get(store_id)
    if profile is None:
        return StoreFit.OK
    brand_fits = [
        profile.brand_fit[brand]
        for brand in understanding.detected_brands
        if brand in profile.brand_fit
    ]
    if StoreFit.BLOCKED in brand_fits:
        return StoreFit.BLOCKED
    if StoreFit.STRONG in brand_fits:
        return StoreFit.STRONG

    category = understanding.detected_category
    if category and category in profile.category_fit:
        return profile.category_fit[category]
    return profile.default_fit
