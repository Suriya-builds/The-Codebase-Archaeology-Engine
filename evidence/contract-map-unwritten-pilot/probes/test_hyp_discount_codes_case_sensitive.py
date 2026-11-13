from shopcart import Discount


def test_code_is_stored_as_given():
    assert Discount.from_config({"code": "ten", "percent": 10}).code == "ten"
