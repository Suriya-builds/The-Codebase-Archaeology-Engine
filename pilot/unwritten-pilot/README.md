# unwritten-pilot

A deliberately small shopping-cart library used as the pilot repository for
the UNWRITTEN verification harness. It has no runtime dependencies.

    from shopcart import Cart, Product, Discount

    cart = Cart()
    cart.add(Product("BK-1", "Novel", "12.50", "books"), quantity=2)
    discount = Discount.from_config({"code": "TEN", "percent": 10})
    cart.total(discount)   # Decimal("22.50")

Run the tests with `python -m pytest tests`.
