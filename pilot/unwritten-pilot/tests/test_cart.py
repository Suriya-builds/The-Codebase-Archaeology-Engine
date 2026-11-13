from decimal import Decimal

import pytest

from shopcart import Cart, InvalidQuantityError, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")


def test_empty_cart_totals_zero():
    cart = Cart()
    assert cart.is_empty()
    assert cart.subtotal() == Decimal("0.00")
    assert cart.total() == Decimal("0.00")


def test_subtotal_sums_line_totals():
    cart = Cart()
    cart.add(NOVEL, 2)
    cart.add(MUG)
    assert cart.subtotal() == Decimal("33.00")


def test_adding_same_product_merges_quantities():
    cart = Cart()
    cart.add(NOVEL)
    cart.add(NOVEL, 3)
    assert [(line.product.sku, line.quantity) for line in cart.lines()] == [("BK-1", 4)]


def test_lines_keep_insertion_order():
    cart = Cart()
    cart.add(MUG)
    cart.add(NOVEL)
    assert [line.product.sku for line in cart.lines()] == ["HM-1", "BK-1"]


def test_remove_deletes_line():
    cart = Cart()
    cart.add(NOVEL)
    cart.add(MUG)
    cart.remove("BK-1")
    assert cart.subtotal() == Decimal("8.00")


def test_remove_unknown_sku_raises_key_error():
    with pytest.raises(KeyError):
        Cart().remove("NOPE")


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "2", True])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(InvalidQuantityError):
        Cart().add(NOVEL, quantity)


def test_product_price_is_normalised_to_cents():
    assert Product("X", "X", "1.005", "misc").price == Decimal("1.01")


def test_float_prices_are_rejected():
    with pytest.raises(TypeError):
        Product("X", "X", 1.5, "misc")
