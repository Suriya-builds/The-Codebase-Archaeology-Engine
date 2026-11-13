from dataclasses import dataclass
from decimal import Decimal

from shopcart.money import to_money


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    category: str

    def __post_init__(self):
        price = to_money(self.price)
        if price < 0:
            raise ValueError("price must not be negative")
        object.__setattr__(self, "price", price)
