import pytest

from shopcart.money import to_money


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_rejected(value):
    with pytest.raises(ValueError):
        to_money(value)
