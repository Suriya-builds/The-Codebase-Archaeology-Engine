"""Feature tests. `Cart.update_quantity` does not exist at the base commit; the
statement declares it, which is the only reason these may fail on a missing name."""
from decimal import Decimal

import pytest

from shopcart import Cart, InvalidQuantityError, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")
PEN = Product("OF-1", "Pen", "0.30", "office")


def cart_of(*items):
    cart = Cart()
    for product, quantity in items:
        cart.add(product, quantity)
    return cart


def snapshot(cart):
    return [(line.product.sku, line.quantity) for line in cart.lines()]


def test_update_replaces_the_quantity():
    cart = cart_of((NOVEL, 2), (MUG, 1))
    assert cart.update_quantity("BK-1", 5) is None
    assert snapshot(cart) == [("BK-1", 5), ("HM-1", 1)]


def test_update_can_lower_the_quantity_and_totals_follow():
    cart = cart_of((NOVEL, 4), (MUG, 1))
    cart.update_quantity("BK-1", 1)
    assert cart.subtotal() == Decimal("20.50")
    assert cart.total() == Decimal("20.50")


def test_zero_removes_the_line():
    cart = cart_of((NOVEL, 2), (MUG, 1))
    cart.update_quantity("BK-1", 0)
    assert snapshot(cart) == [("HM-1", 1)]
    cart.update_quantity("HM-1", 0)
    assert cart.is_empty()


def test_unknown_sku_raises_key_error_and_changes_nothing():
    cart = cart_of((NOVEL, 2))
    with pytest.raises(KeyError):
        cart.update_quantity("NOPE", 3)
    assert snapshot(cart) == [("BK-1", 2)]


@pytest.mark.parametrize("quantity", [-1, 1.5, True])
def test_unknown_sku_is_reported_before_an_invalid_quantity(quantity):
    cart = cart_of((NOVEL, 2))
    with pytest.raises(KeyError):
        cart.update_quantity("NOPE", quantity)


@pytest.mark.parametrize("quantity", [-1, -100, 1.5, "2", None, True, False])
def test_invalid_quantity_is_rejected_and_changes_nothing(quantity):
    cart = cart_of((NOVEL, 2), (MUG, 1))
    with pytest.raises(InvalidQuantityError):
        cart.update_quantity("BK-1", quantity)
    assert snapshot(cart) == [("BK-1", 2), ("HM-1", 1)]


def test_updated_line_keeps_its_position():
    cart = cart_of((NOVEL, 1), (MUG, 1), (PEN, 1))
    cart.update_quantity("BK-1", 7)
    cart.update_quantity("HM-1", 2)
    assert snapshot(cart) == [("BK-1", 7), ("HM-1", 2), ("OF-1", 1)]
