"""DELIBERATE DEFECT (see task.json provenance).

This "feature test" only exercises a cart that satisfies every condition. The
base commit ignores the eligibility key, so the test already passes there: it
cannot tell whether the feature exists.
"""
from decimal import Decimal

from shopcart import Cart, Discount, Product


def test_discount_applies_when_cart_meets_the_minimum():
    cart = Cart()
    cart.add(Product("BK-1", "Novel", "12.50", "books"), 4)
    discount = Discount.from_config(
        {"code": "TEN", "percent": 10, "eligibility": {"min_subtotal": "20.00"}})
    assert cart.total(discount) == Decimal("45.00")
