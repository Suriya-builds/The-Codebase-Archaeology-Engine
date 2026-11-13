from dataclasses import dataclass
from decimal import Decimal

from shopcart.errors import InvalidDiscountError
from shopcart.money import round_money


@dataclass(frozen=True)
class Discount:
    """A percentage discount identified by a code."""

    code: str
    percent: Decimal

    @classmethod
    def from_config(cls, config):
        """Build a discount from a plain mapping, e.g. parsed JSON.

        Keys this version does not understand are ignored, so that newer
        configuration files keep loading.
        """
        if not isinstance(config, dict):
            raise InvalidDiscountError("discount config must be a mapping")
        code = config.get("code")
        if not isinstance(code, str) or not code.strip():
            raise InvalidDiscountError("discount code must be a non-empty string")
        percent = _parse_percent(config.get("percent"))
        return cls(code=code.strip().upper(), percent=percent)

    def amount_for(self, cart):
        """The amount this discount takes off the given cart."""
        return round_money(cart.subtotal() * self.percent / 100)


def _parse_percent(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, str, Decimal)):
        raise InvalidDiscountError("percent must be a number")
    try:
        percent = Decimal(raw)
    except ArithmeticError as exc:
        raise InvalidDiscountError("percent must be a number") from exc
    if not percent.is_finite() or percent <= 0 or percent > 100:
        raise InvalidDiscountError("percent must be greater than 0 and at most 100")
    return percent
