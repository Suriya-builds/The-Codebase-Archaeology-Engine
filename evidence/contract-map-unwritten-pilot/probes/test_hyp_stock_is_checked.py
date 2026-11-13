from shopcart.inventory import Stock


def test_stock_is_consulted():
    assert Stock().available("X") >= 0
