# Configurable discount eligibility

## Background

`shopcart.Discount.from_config` builds a percentage discount from a plain
mapping, and `Cart.total(discount)` subtracts `discount.amount_for(cart)` from
the cart subtotal. Today every discount applies to the whole cart, always.

## Requirement

A discount configuration may carry an optional `eligibility` mapping that
limits when, and to which lines, the discount applies:

    {"code": "BOOKS10", "percent": 10,
     "eligibility": {"min_subtotal": "30.00", "categories": ["books"]}}

### Behaviour

1. **Eligible lines.** If `categories` is present, only cart lines whose
   product category is exactly equal (case-sensitive) to one of the listed
   categories are eligible. If `categories` is absent, every line is eligible.
2. **Eligible subtotal.** The eligible subtotal is the sum of the line totals
   of the eligible lines.
3. **Minimum.** If the eligible subtotal is lower than `min_subtotal`, the
   discount amount is `0.00`. An eligible subtotal exactly equal to
   `min_subtotal` qualifies. `min_subtotal` defaults to `0`.
4. **Amount.** Otherwise the discount amount is the percentage of the
   *eligible subtotal* (not of the whole cart), rounded half up to whole
   cents, as existing discounts are.
5. An ineligible discount is not an error: `Cart.total(discount)` returns the
   undiscounted subtotal and `discount.amount_for(cart)` returns `0.00`.

### Validation

`Discount.from_config` raises `InvalidDiscountError` when:

- `eligibility` is present and not a mapping;
- `min_subtotal` is not a valid money value (a `str`, `int` or `Decimal`
  amount; floats are not money values in this library) or is negative;
- `categories` is not a list, is an empty list, or contains anything other
  than non-blank strings.

### Must keep working

- A configuration without `eligibility` behaves exactly as before.
- `"eligibility": {}` places no restriction on the discount.
- Unknown keys, inside or outside `eligibility`, are still ignored.
- `min_subtotal` of `0` is valid.

## Interface

Only the public behaviour of `Discount.from_config`, `Discount.amount_for` and
`Cart.total` is specified. How eligibility is represented internally is up to
the implementation.

## Out of scope

Stacking several discounts, per-customer eligibility and date-based expiry.
