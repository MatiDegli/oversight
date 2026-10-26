# shop

A small shop library: a product catalogue, tiered pricing, stock reservations, a cart and orders. It uses only the standard library, and all amounts are integer cents.

## Modules

- `shop/models.py`: `Product`, `OrderLine`, `Customer` and `PremiumCustomer`, plus `format_cents`.
- `shop/pricing.py`: `compute_price` (bulk tiers and extra discounts), coupons, tax and shipping.
- `shop/inventory.py`: `Inventory`, which tracks stock and reserves units for orders.
- `shop/cart.py`: `Cart`, which merges lines by SKU and totals them.
- `shop/orders.py`: `place_order`, `cancel_order` and a category revenue report.

## Tests

```bash
python -m pytest
```
