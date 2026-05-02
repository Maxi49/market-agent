from app.models import Product, ProductCondition
from app.ranking import rank_products
from tests.typing_helpers import http_url


def product(title: str, price: float | None, position: int = 1, condition: ProductCondition = ProductCondition.NEW) -> Product:
    return Product(
        store_id="test",
        store_name="Test",
        position=position,
        title=title,
        price=price,
        currency="$",
        product_url=http_url(f"https://example.com/{position}"),
        condition=condition,
    )


def test_ranking_filters_accessories_and_orders_by_price() -> None:
    results = rank_products(
        "iphone 15",
        [
            product("Protector vidrio templado iPhone 15", 4000, 1),
            product("Spigen Ultra Hybrid Magfit para iPhone 15 Pro", 109999, 4),
            product("Apple iPhone 15 128GB", 1500000, 2),
            product("Apple iPhone 15 256GB", 1700000, 3),
        ],
        limit=3,
    )

    assert [item.title for item in results] == [
        "Apple iPhone 15 128GB",
        "Apple iPhone 15 256GB",
    ]


def test_ranking_penalizes_refurbished_unless_query_asks_for_it() -> None:
    new_item = product("Apple iPhone 15 128GB", 1500000, 1)
    refurbished = product("Apple iPhone 15 reacondicionado", 900000, 2, ProductCondition.REFURBISHED)

    assert rank_products("iphone 15", [refurbished, new_item], 2)[0] == new_item
    assert rank_products("iphone 15 reacondicionado", [refurbished, new_item], 2)[0] == refurbished
