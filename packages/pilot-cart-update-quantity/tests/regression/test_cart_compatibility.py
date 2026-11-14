"""Behaviour that must be the same before and after the feature."""
from decimal import Decimal

from shopcart import Cart, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")


def test_add_still_merges_instead_of_replacing():
    cart = Cart()
    cart.add(NOVEL, 2)
    cart.add(NOVEL, 3)
    assert [(line.product.sku, line.quantity) for line in cart.lines()] == [("BK-1", 5)]


def test_remove_then_add_still_moves_the_line_to_the_end():
    cart = Cart()
    cart.add(NOVEL)
    cart.add(MUG)
    cart.remove("BK-1")
    cart.add(NOVEL)
    assert [line.product.sku for line in cart.lines()] == ["HM-1", "BK-1"]
    assert cart.subtotal() == Decimal("20.50")
