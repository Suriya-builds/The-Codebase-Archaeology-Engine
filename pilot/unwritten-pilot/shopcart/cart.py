from dataclasses import dataclass

from shopcart.catalog import Product
from shopcart.errors import InvalidQuantityError
from shopcart.money import ZERO


@dataclass(frozen=True)
class CartLine:
    product: Product
    quantity: int

    def line_total(self):
        return self.product.price * self.quantity


class Cart:
    """An ordered collection of products and quantities."""

    def __init__(self):
        self._lines = {}

    def add(self, product, quantity=1):
        _check_quantity(quantity)
        existing = self._lines.get(product.sku)
        if existing is not None:
            quantity += existing.quantity
        self._lines[product.sku] = CartLine(product, quantity)

    def remove(self, sku):
        if sku not in self._lines:
            raise KeyError(sku)
        del self._lines[sku]

    def lines(self):
        """Lines in the order their products were first added."""
        return list(self._lines.values())

    def is_empty(self):
        return not self._lines

    def subtotal(self):
        return sum((line.line_total() for line in self._lines.values()), ZERO)

    def total(self, discount=None):
        """Subtotal minus the discount amount, never below zero."""
        subtotal = self.subtotal()
        if discount is None:
            return subtotal
        return max(subtotal - discount.amount_for(self), ZERO)


def _check_quantity(quantity):
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidQuantityError("quantity must be an integer")
    if quantity < 1:
        raise InvalidQuantityError("quantity must be at least 1")
