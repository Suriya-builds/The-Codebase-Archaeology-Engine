"""Feature tests: every test here fails before the feature exists."""
from decimal import Decimal

import pytest

from shopcart import Cart, Discount, InvalidDiscountError, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")
PEN = Product("OF-1", "Pen", "0.30", "office")


def cart_of(*items):
    cart = Cart()
    for product, quantity in items:
        cart.add(product, quantity)
    return cart


def discount(percent=10, **eligibility):
    return Discount.from_config({"code": "TEST", "percent": percent, "eligibility": eligibility})


def test_discount_is_not_applied_below_min_subtotal():
    cart = cart_of((NOVEL, 2), (MUG, 1))  # 33.00
    assert cart.total(discount(min_subtotal="50.00")) == Decimal("33.00")


def test_min_subtotal_boundary_is_inclusive():
    cart = cart_of((NOVEL, 2), (MUG, 1))  # 33.00
    assert cart.total(discount(min_subtotal="33.00")) == Decimal("29.70")
    assert cart.total(discount(min_subtotal="33.01")) == Decimal("33.00")


def test_ineligible_discount_amount_is_zero():
    cart = cart_of((MUG, 1))
    assert discount(min_subtotal="8.01").amount_for(cart) == Decimal("0.00")


def test_category_restricts_discount_to_matching_lines():
    cart = cart_of((NOVEL, 2), (MUG, 1))  # books 25.00, home 8.00
    assert cart.total(discount(categories=["books"])) == Decimal("30.50")


def test_several_categories_are_all_eligible():
    cart = cart_of((NOVEL, 2), (MUG, 1), (PEN, 10))  # 25.00 + 8.00 + 3.00
    assert cart.total(discount(categories=["books", "office"])) == Decimal("33.20")


def test_no_eligible_lines_means_no_discount():
    cart = cart_of((NOVEL, 2), (MUG, 1))
    assert cart.total(discount(categories=["garden"])) == Decimal("33.00")


def test_category_match_is_case_sensitive():
    cart = cart_of((NOVEL, 2))
    assert cart.total(discount(categories=["Books"])) == Decimal("25.00")


def test_min_subtotal_is_measured_on_eligible_lines_only():
    cart = cart_of((NOVEL, 2), (MUG, 2))  # books 25.00, whole cart 41.00
    assert cart.total(discount(min_subtotal="30.00", categories=["books"])) == Decimal("41.00")


def test_amount_is_rounded_half_up_on_the_eligible_subtotal():
    cart = cart_of((PEN, 1), (MUG, 1))  # 15% of 0.30 is 0.045
    restricted = discount(percent=15, categories=["office"])
    assert restricted.amount_for(cart) == Decimal("0.05")
    assert cart.total(restricted) == Decimal("8.25")


@pytest.mark.parametrize("eligibility", ["books", ["books"], 5, True])
def test_eligibility_must_be_a_mapping(eligibility):
    with pytest.raises(InvalidDiscountError):
        Discount.from_config({"code": "BAD", "percent": 10, "eligibility": eligibility})


@pytest.mark.parametrize("min_subtotal", ["-0.01", -5, "abc", 1.5, None, True])
def test_invalid_min_subtotal_is_rejected(min_subtotal):
    with pytest.raises(InvalidDiscountError):
        discount(min_subtotal=min_subtotal)


@pytest.mark.parametrize("categories", [[], "books", ("books",), [7], ["books", "  "], [""]])
def test_invalid_categories_are_rejected(categories):
    with pytest.raises(InvalidDiscountError):
        discount(categories=categories)
