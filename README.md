# SwiggyOpt — phase 1

Minimal FastAPI application that uses Swiggy's officially documented OAuth 2.1
Authorization Code flow with PKCE and Dynamic Client Registration, then makes a
single read-only Food MCP `get_addresses` call.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set `APP_SESSION_SECRET` to a random value.
4. Run: `uvicorn app.main:app --host 127.0.0.1 --port 80`
5. Open `http://localhost`, select the authentication link, and complete
   Swiggy's browser-based phone/OTP consent flow.

The result page displays the actual MCP response. The access token and
authorization code are neither logged nor persisted by this app.

## Menu discovery API

After authentication, choose an address returned by `GET /api/addresses`:

```powershell
Invoke-RestMethod -Method Put http://localhost/api/addresses/selected `
  -ContentType 'application/json' -Body '{"address_id":"<saved-address-id>"}' `
  -WebSession $session
```

Use the same browser session (or PowerShell `WebRequestSession`) to request a
category page:

`GET /api/restaurants/{restaurant_id}/menu?page=1&page_size=5`

Add `q=<dish>` to retrieve the officially documented, restaurant-scoped
customization details for matching items, including exact variant, group, and
add-on IDs:

`GET /api/restaurants/{restaurant_id}/menu?q=biryani&page=1&page_size=5`

Swiggy's complete-menu tool supplies compact item data only. Variants and
add-ons are supplied by `search_menu` for a query; this app attaches those
details to a category item only when the returned Swiggy item IDs are exactly
equal. It does not infer matches from names.

## Offer discovery API

`GET /api/restaurants/{restaurant_id}/offers` retrieves Swiggy's documented
read-only coupon sections for the selected address. Add `coupon_code=<code>` to
check a particular coupon where Swiggy supports it. The API preserves coupon
cards, terms, applicability status, and any additional fields Swiggy returns;
it does not calculate discounts or infer item eligibility.

Swiggy defines `applicable` and `applicabilityStatus` against the **current
cart**. Exact item-level eligibility is not exposed by `fetch_food_coupons` and
is intentionally deferred to the cart phase.

## Candidate generation API

`POST /api/candidates` runs a read-only candidate pipeline for the selected
address. It calls `search_restaurants`, then performs restaurant-scoped
`search_menu` calls only for the limited, available restaurant set. It expands
legacy variants and documented `variantsV2` choice combinations into distinct
candidates, preserving IDs, group IDs, raw menu prices, stock state, category
when Swiggy provides it, and possible add-ons.

Example body:

```json
{
  "query": "biryani",
  "candidate_limit": 20,
  "filters": {
    "require_available": true,
    "max_restaurants": 6,
    "max_variant_combinations_per_item": 16,
    "max_menu_price": 500
  }
}
```

The endpoint returns per-stage counts and pagination cursors. It does not call
any cart, coupon, pricing, or ranking operation.

Swiggy documents redirect URIs as exact-match and publishes
`http://localhost/callback` for local development. If port 80 is unavailable on
your machine or you need a different redirect URI, request that exact URI from
Swiggy at `builders@swiggy.in` before changing `SWIGGY_REDIRECT_URI`.

## Official references

- https://mcp.swiggy.com/builders/docs/start/authenticate/
- https://mcp.swiggy.com/builders/docs/start/developer/
- https://mcp.swiggy.com/builders/docs/reference/food/get_addresses/
- https://mcp.swiggy.com/builders/docs/reference/food/get_restaurant_menu/
- https://mcp.swiggy.com/builders/docs/reference/food/search_menu/
- https://mcp.swiggy.com/builders/docs/reference/food/fetch_food_coupons/
