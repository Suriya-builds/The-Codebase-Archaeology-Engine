from shopcart import CartError, InvalidDiscountError, InvalidQuantityError


def test_errors_share_a_base_class():
    assert issubclass(InvalidQuantityError, CartError)
    assert issubclass(InvalidDiscountError, CartError)
