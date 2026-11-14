# Update the quantity of a cart line

## Background

`shopcart.cart.Cart.add` adds units of a product and merges them into an
existing line. There is no way to set a line to a given quantity: lowering a
quantity today means removing the line and adding it again, which also moves
it to the end of the cart.

## Requirement

Add a method that sets the quantity of a product that is already in the cart.

## Interface

In module `shopcart.cart`, class `Cart` gains one public method:

    update_quantity(self, sku, quantity)

It returns `None`.

## Acceptance criteria

- **AC-1** For a product in the cart and a positive integer `quantity`, the
  line's quantity becomes exactly `quantity`. It replaces the old quantity; it
  is not added to it. Subtotals and totals reflect the new quantity.
- **AC-2** A `quantity` of `0` removes the line from the cart.
- **AC-3** If no line has the given `sku`, `KeyError` is raised and the cart
  is unchanged. The `sku` is checked before the quantity, so an unknown `sku`
  raises `KeyError` even when the quantity is also invalid.
- **AC-4** A negative `quantity`, or one that is not an integer (`bool`
  values are not accepted as integers), raises `InvalidQuantityError` and
  leaves the cart unchanged.
- **AC-5** Updating a line does not change its position: `Cart.lines()` keeps
  the order in which products were first added.

## Must keep working

`Cart.add`, `Cart.remove`, `Cart.lines`, `Cart.subtotal` and `Cart.total`
behave exactly as before.

## Out of scope

Quantity limits, stock checks and bulk updates of several lines.
