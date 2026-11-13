import pytest

from shopcart import Cart, InvalidQuantityError, Product


def test_quantity_above_limit_is_rejected():
    with pytest.raises(InvalidQuantityError):
        Cart().add(Product("X", "X", "1", "misc"), 1000)
