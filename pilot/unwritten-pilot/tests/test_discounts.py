from decimal import Decimal

import pytest

from shopcart import Cart, Discount, InvalidDiscountError, Product

NOVEL = Product("BK-1", "Novel", "12.50", "books")
MUG = Product("HM-1", "Mug", "8.00", "home")


def cart_of(*items):
    cart = Cart()
    for product, quantity in items:
        cart.add(product, quantity)
    return cart


def test_percentage_discount_applies_to_whole_subtotal():
    discount = Discount.from_config({"code": "TEN", "percent": 10})
    assert cart_of((NOVEL, 2), (MUG, 1)).total(discount) == Decimal("29.70")


def test_discount_amount_rounds_half_up():
    discount = Discount.from_config({"code": "ODD", "percent": "15"})
    cart = cart_of((Product("P", "Pen", "0.30", "office"), 1))
    assert discount.amount_for(cart) == Decimal("0.05")
    assert cart.total(discount) == Decimal("0.25")


def test_full_discount_makes_cart_free():
    discount = Discount.from_config({"code": "FREE", "percent": 100})
    assert cart_of((MUG, 3)).total(discount) == Decimal("0.00")


def test_code_is_trimmed_and_uppercased():
    assert Discount.from_config({"code": "  ten ", "percent": 10}).code == "TEN"


def test_unknown_config_keys_are_ignored():
    discount = Discount.from_config({"code": "TEN", "percent": 10, "campaign": "spring"})
    assert discount.percent == Decimal("10")


@pytest.mark.parametrize("percent", [0, -5, 101, "abc", None, True, 2.5])
def test_invalid_percent_is_rejected(percent):
    with pytest.raises(InvalidDiscountError):
        Discount.from_config({"code": "BAD", "percent": percent})


@pytest.mark.parametrize("code", [None, "", "   ", 7])
def test_invalid_code_is_rejected(code):
    with pytest.raises(InvalidDiscountError):
        Discount.from_config({"code": code, "percent": 10})


def test_config_must_be_a_mapping():
    with pytest.raises(InvalidDiscountError):
        Discount.from_config(["TEN", 10])
