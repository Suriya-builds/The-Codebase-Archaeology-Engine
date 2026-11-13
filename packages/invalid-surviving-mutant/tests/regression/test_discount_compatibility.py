"""Behaviour that must be the same before and after the feature.

These tests pass at the base commit and must keep passing.
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


def test_discount_without_eligibility_applies_to_whole_cart():
    discount = Discount.from_config({"code": "TEN", "percent": 10})
    cart = cart_of((NOVEL, 2), (MUG, 1))
    assert discount.amount_for(cart) == Decimal("3.30")
    assert cart.total(discount) == Decimal("29.70")


def test_empty_eligibility_places_no_restriction():
    discount = Discount.from_config({"code": "TEN", "percent": 10, "eligibility": {}})
    assert cart_of((NOVEL, 2), (MUG, 1)).total(discount) == Decimal("29.70")


def test_zero_min_subtotal_is_valid_and_always_met():
    discount = Discount.from_config(
        {"code": "TEN", "percent": 10, "eligibility": {"min_subtotal": 0}})
    assert cart_of((MUG, 1)).total(discount) == Decimal("7.20")


def test_min_subtotal_only_keeps_every_line_eligible():
    discount = Discount.from_config(
        {"code": "TEN", "percent": 10, "eligibility": {"min_subtotal": "10.00"}})
    assert cart_of((NOVEL, 2), (MUG, 1)).total(discount) == Decimal("29.70")


def test_unknown_eligibility_keys_are_ignored():
    discount = Discount.from_config(
        {"code": "TEN", "percent": 10, "eligibility": {"weekday": "monday"}})
    assert cart_of((MUG, 1)).total(discount) == Decimal("7.20")


def test_cart_whose_lines_are_all_eligible_gets_the_full_discount():
    discount = Discount.from_config(
        {"code": "TEN", "percent": 10, "eligibility": {"categories": ["books", "home"]}})
    assert cart_of((NOVEL, 2), (MUG, 1)).total(discount) == Decimal("29.70")
