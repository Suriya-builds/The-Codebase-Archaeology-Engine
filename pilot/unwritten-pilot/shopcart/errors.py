class CartError(Exception):
    """Base class for all shopcart errors."""


class InvalidQuantityError(CartError):
    """Raised when a quantity is not a positive integer."""


class InvalidDiscountError(CartError):
    """Raised when a discount configuration is malformed."""
