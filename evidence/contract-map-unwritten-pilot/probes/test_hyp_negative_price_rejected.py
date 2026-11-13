import pytest

from shopcart import Product


def test_negative_price_is_rejected():
    with pytest.raises(ValueError):
        Product("X", "X", "-0.01", "misc")


def test_zero_price_is_allowed():
    assert Product("X", "X", "0", "misc").price == 0
