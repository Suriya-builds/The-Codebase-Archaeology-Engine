from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_money(value):
    """Convert a str, int or Decimal to a Decimal rounded to whole cents.

    Floats are rejected: binary floating point cannot represent most prices.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("money values must be str, int or Decimal")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"not a money value: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"not a money value: {value!r}")
    return round_money(amount)


def round_money(amount):
    """Round half up to whole cents."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)
