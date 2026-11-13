"""DELIBERATE DEFECT (see task.json provenance).

Every test here fails at the base commit and passes with the reference
implementation, yet the suite is weak: it never probes the minimum-subtotal
boundary, never combines both conditions and never checks validation.
"""
from decimal import Decimal

from shopcart import Cart, Discount, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")


def cart_of(*items):
    cart = Cart()
    for product, quantity in items:
        cart.add(product, quantity)
    return cart


def discount(**eligibility):
    return Discount.from_config({"code": "TEST", "percent": 10, "eligibility": eligibility})


def test_discount_is_not_applied_below_min_subtotal():
    cart = cart_of((NOVEL, 2), (MUG, 1))  # 33.00
    assert cart.total(discount(min_subtotal="50.00")) == Decimal("33.00")


def test_category_restricts_discount_to_matching_lines():
    cart = cart_of((NOVEL, 2), (MUG, 1))  # books 25.00, home 8.00
    assert cart.total(discount(categories=["books"])) == Decimal("30.50")


def test_no_eligible_lines_means_no_discount():
    cart = cart_of((NOVEL, 2), (MUG, 1))
    assert cart.total(discount(categories=["garden"])) == Decimal("33.00")
