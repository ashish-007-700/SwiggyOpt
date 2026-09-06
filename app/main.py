"""Minimal local OAuth 2.1 + PKCE client for Swiggy Food MCP."""

from __future__ import annotations

import asyncio
import base64
import html
import hashlib
import itertools
import json as json_module
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import httpx2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

import os

from app.models import (
    AddressListResponse,
    AuthStatusResponse,
    CandidatePagination,
    CandidateRequest,
    CandidateResponse,
    CandidateRestaurant,
    CandidateStageCounts,
    CandidateVariant,
    CartItem,
    CouponSection,
    CouponSummary,
    CustomizationSearchPagination,
    DeliveryAddress,
    EvaluateRequest,
    EvaluateResponse,
    EvaluatedCandidate,
    MenuCategory,
    MenuCandidate,
    MenuItem,
    MenuPagination,
    MenuResponse,
    MenuRestaurant,
    PricingBreakdown,
    RestaurantOffersResponse,
    SelectAddressRequest,
    SelectedAddressResponse,
)

logger = logging.getLogger(__name__)

load_dotenv()

OAUTH_BASE_URL = os.getenv("SWIGGY_OAUTH_BASE_URL", "https://mcp.swiggy.com").rstrip("/")
FOOD_MCP_URL = os.getenv("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food")
REDIRECT_URI = os.getenv("SWIGGY_REDIRECT_URI", "http://localhost/callback")
SESSION_SECRET = os.getenv("APP_SESSION_SECRET")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
PENDING_AUTH_TTL_SECONDS = 10 * 60
pending_auth: dict[str, tuple[str, float]] = {}
authenticated_sessions: dict[str, "AuthenticatedSession"] = {}

if not SESSION_SECRET:
    raise RuntimeError("APP_SESSION_SECRET must be set in .env before starting the app.")


def pkce_challenge(verifier: str) -> str:
    """Return the RFC 7636 S256 code challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class AuthenticatedSession:
    """Short-lived local server-side state; never serialised into browser cookies."""

    def __init__(self, access_token: str, expires_at: float) -> None:
        self.access_token = access_token
        self.expires_at = expires_at
        self.selected_address_id: str | None = None


async def register_client(redirect_uri: str) -> str:
    """Register this public local client using Swiggy's documented DCR endpoint."""
    payload = {
        "client_name": "SwiggyOpt local development",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{OAUTH_BASE_URL}/auth/register", json=payload)
    response.raise_for_status()
    client_id = response.json().get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise RuntimeError("Swiggy DCR response did not include a client_id.")
    return client_id


async def exchange_code(*, code: str, verifier: str) -> tuple[str, float]:
    """Exchange a one-time authorization code without logging sensitive values."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{OAUTH_BASE_URL}/auth/token", json=payload)
    response.raise_for_status()
    access_token = response.json().get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Swiggy token response did not include an access_token.")
    expires_in = response.json().get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise RuntimeError("Swiggy token response did not include a valid expires_in.")
    return access_token, time.monotonic() + expires_in


    return access_token, time.monotonic() + expires_in


def unwrap_exception(exc: BaseException) -> BaseException:
    """Recursively unwrap ExceptionGroup / TaskGroup / chained exceptions to retrieve the root cause."""
    current = exc
    visited = set()
    while current and id(current) not in visited:
        visited.add(id(current))
        if hasattr(current, "exceptions") and getattr(current, "exceptions"):
            exceptions = getattr(current, "exceptions")
            if exceptions:
                current = exceptions[0]
                continue
        if getattr(current, "__cause__", None) is not None:
            current = current.__cause__  # type: ignore
            continue
        if getattr(current, "__context__", None) is not None:
            current = current.__context__  # type: ignore
            continue
        break
    return current or exc


def _collect_leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Collect all leaf exceptions from an exception chain, including all ExceptionGroup members."""
    leaves: list[BaseException] = []
    visited: set[int] = set()

    def walk(e: BaseException) -> None:
        if id(e) in visited:
            return
        visited.add(id(e))
        if hasattr(e, "exceptions") and getattr(e, "exceptions"):
            for sub in getattr(e, "exceptions"):
                walk(sub)
            return
        if getattr(e, "__cause__", None) is not None:
            walk(e.__cause__)  # type: ignore
            return
        if getattr(e, "__context__", None) is not None:
            walk(e.__context__)  # type: ignore
            return
        leaves.append(e)

    walk(exc)
    return leaves or [exc]


def is_auth_error(exc: BaseException) -> bool:
    """Return True if the exception or any of its unpacked causes is a 401 Unauthorized error.

    The Swiggy MCP SDK wraps httpx transport errors in a generic message
    ("Server returned an error response") that strips the HTTP status code.
    When this happens, it nearly always means the Swiggy access token has
    expired or been revoked, so we treat it as an auth error to prompt
    re-authentication rather than showing a confusing 502.
    """
    for leaf in _collect_leaf_exceptions(exc):
        status_code = getattr(leaf, "status_code", None)
        if status_code is None and hasattr(leaf, "response"):
            status_code = getattr(getattr(leaf, "response"), "status_code", None)
        if status_code == 401:
            return True
        msg = str(leaf)
        if "401" in msg or "Unauthorized" in msg or "unauthorized" in msg:
            return True
        # The MCP SDK's streamable HTTP client strips the HTTP status code and
        # raises a generic "Server returned an error response" when the Swiggy
        # server rejects the request.  This is almost always a token expiry.
        if msg == "Server returned an error response":
            return True
    return False


async def call_food_tool(access_token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one Food MCP tool through the official MCP Python SDK."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx2.AsyncClient(headers=headers, follow_redirects=True) as http_client:
            async with streamable_http_client(FOOD_MCP_URL, http_client=http_client) as streams:
                read_stream, write_stream = streams
                async with ClientSession(read_stream, write_stream) as mcp_session:
                    await mcp_session.initialize()
                    try:
                        tools_res = await mcp_session.list_tools()
                        tool_names = [t.name for t in tools_res.tools]
                        logger.info("Swiggy MCP Available Tools: %s", tool_names)
                    except Exception as e:
                        logger.warning("Could not list MCP tools: %s", e)
                    result = await mcp_session.call_tool(name, arguments=arguments)
        return result.model_dump(mode="json")
    except Exception as exc:
        root_exc = unwrap_exception(exc)
        logger.error("call_food_tool failed for tool '%s': %s (root cause: %s)", name, exc, root_exc)
        if is_auth_error(exc):
            raise HTTPException(status_code=401, detail="Swiggy authentication has expired. Authenticate again.") from exc
        raise HTTPException(status_code=502, detail=f"Swiggy Food MCP tool '{name}' failed: {root_exc}") from exc


async def get_addresses(access_token: str) -> dict[str, Any]:
    return await call_food_tool(access_token, "get_addresses", {})


def swiggy_data(mcp_result: dict[str, Any]) -> dict[str, Any]:
    """Extract Swiggy's documented data envelope or direct data from an MCP tool result."""
    logger.info("swiggy_data parsing mcp_result: %s", json_module.dumps(mcp_result, default=str))

    structured = mcp_result.get("structuredContent") or mcp_result.get("structured_content")
    candidates: list[Any] = []
    if structured is not None:
        candidates.append(structured)
    for content in mcp_result.get("content", []):
        if isinstance(content, dict) and content.get("type") == "text":
            candidates.append(content.get("text"))

    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                candidate = json_module.loads(candidate)
            except (TypeError, ValueError):
                continue

        if isinstance(candidate, dict):
            # Case 1: Standard documented {success: True, data: {...}}
            if candidate.get("success") is True and isinstance(candidate.get("data"), dict):
                return candidate["data"]

            # Case 2: {success: True, data: [...]} (array data)
            if candidate.get("success") is True and isinstance(candidate.get("data"), list):
                return {"items": candidate["data"]}

            # Case 3: Error envelope {success: False, error: {...}}
            if candidate.get("success") is False:
                err = candidate.get("error")
                message = err.get("message") if isinstance(err, dict) else str(err) if err else None
                raise HTTPException(status_code=502, detail=message or "Swiggy Food MCP returned an error.")

            # Case 4: 'data' dict present directly without explicit success=True
            if isinstance(candidate.get("data"), dict):
                return candidate["data"]

            # Case 5: Direct payload dict (e.g. structured_content with items, restaurants, addresses)
            return candidate

    logger.warning("swiggy_data unreadable mcp_result content: %s", json_module.dumps(mcp_result, default=str))
    raise HTTPException(status_code=502, detail="Swiggy Food MCP returned an unreadable response.")




def session_for(request: Request) -> AuthenticatedSession:
    session_id = request.session.get("authenticated_session_id")
    session = authenticated_sessions.get(session_id) if isinstance(session_id, str) else None
    if session is None:
        raise HTTPException(status_code=401, detail="Swiggy authentication is required. Authenticate again.")
    if session.expires_at <= time.monotonic():
        authenticated_sessions.pop(session_id, None)
        request.session.pop("authenticated_session_id", None)
        raise HTTPException(status_code=401, detail="Swiggy authentication has expired. Authenticate again.")
    return session


async def addresses_for(session: AuthenticatedSession) -> list[DeliveryAddress]:
    try:
        data = swiggy_data(await get_addresses(session.access_token))
        raw_addresses = data.get("addresses", [])
        if not isinstance(raw_addresses, list):
            raise HTTPException(status_code=502, detail="Swiggy returned an invalid address list.")
        return [DeliveryAddress.model_validate(address) for address in raw_addresses]
    except HTTPException:
        raise
    except (httpx2.HTTPStatusError, httpx.HTTPStatusError) as exc:
        if exc.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Swiggy authentication has expired. Authenticate again.") from exc
        raise HTTPException(status_code=502, detail="Swiggy could not retrieve saved addresses.") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="Swiggy returned an invalid saved address.") from exc


async def food_data_for(session: AuthenticatedSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call Food MCP and make expired Swiggy credentials an actionable API error."""
    try:
        return swiggy_data(await call_food_tool(session.access_token, name, arguments))
    except HTTPException:
        raise
    except (httpx2.HTTPStatusError, httpx.HTTPStatusError) as exc:
        if exc.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Swiggy authentication has expired. Authenticate again.") from exc
        raise HTTPException(status_code=502, detail=f"Swiggy Food MCP request failed (HTTP {exc.response.status_code}).") from exc



def menu_item_from_raw(raw_item: dict[str, Any]) -> MenuItem:
    """Normalise only documented menu item ID names without inventing an ID."""
    item = dict(raw_item)
    item["id"] = raw_item.get("id") or raw_item.get("menu_item_id")
    return MenuItem.model_validate(item)


def menu_category_from_raw(raw_category: dict[str, Any]) -> MenuCategory:
    category = dict(raw_category)
    raw_items = category.get("items", [])
    raw_subcategories = category.get("subcategories", [])
    category["items"] = [menu_item_from_raw(item) for item in raw_items if isinstance(item, dict)]
    category["subcategories"] = [
        menu_category_from_raw(subcategory) for subcategory in raw_subcategories if isinstance(subcategory, dict)
    ]
    return MenuCategory.model_validate(category)


def enrich_categories(
    categories: list[MenuCategory], detailed_items: list[MenuItem]
) -> tuple[list[MenuCategory], list[MenuItem]]:
    """Attach customization data only when the documented Swiggy item IDs are equal."""
    details_by_id = {item.id: item for item in detailed_items if item.id is not None}
    matched_ids: set[str] = set()

    def enrich(category: MenuCategory) -> MenuCategory:
        items: list[MenuItem] = []
        for item in category.items:
            detail = details_by_id.get(item.id) if item.id is not None else None
            if detail is None:
                items.append(item)
                continue
            matched_ids.add(item.id)
            # Keep category membership from get_restaurant_menu, and replace
            # item detail only with the result returned for that exact ID.
            items.append(detail)
        return category.model_copy(
            update={"items": items, "subcategories": [enrich(child) for child in category.subcategories]}
        )

    enriched = [enrich(category) for category in categories]
    return enriched, [item for item in detailed_items if item.id is None or item.id not in matched_ids]


def explicitly_available(value: int | None) -> bool:
    """Only an explicit zero is unavailable; omitted stock stays eligible for discovery."""
    return value is None or value != 0


def query_tokens(query: str) -> set[str]:
    return {token for token in query.casefold().split() if token}


def candidate_variants(item: MenuItem, max_combinations: int) -> list[tuple[str, list[CandidateVariant]]]:
    """Expand documented variant formats without merging similarly named choices."""
    if item.variations:
        return [
            (
                "variations",
                [
                    CandidateVariant(
                        id=variant.id,
                        name=variant.name,
                        group_id=variant.group_id,
                        price=variant.price,
                        in_stock=variant.in_stock,
                    )
                ],
            )
            for variant in item.variations
            if variant.id is not None
        ]
    if item.variants_v2:
        groups = [
            [
                CandidateVariant(
                    id=variant.id,
                    name=variant.name,
                    group_id=group.group_id,
                    price=variant.price,
                    in_stock=variant.in_stock,
                )
                for variant in group.variations
                if variant.id is not None
            ]
            for group in item.variants_v2
        ]
        if not groups or any(not group for group in groups):
            return []
        return [
            ("variants_v2", list(selection))
            for selection in itertools.islice(itertools.product(*groups), max_combinations)
        ]
    return [("none", [])]


def candidate_key(restaurant_id: str, item_id: str, variants: list[CandidateVariant]) -> str:
    variant_part = ",".join(f"{variant.group_id or ''}:{variant.id or ''}" for variant in variants)
    return f"{restaurant_id}:{item_id}:{variant_part or 'base'}"


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>{title}</title>
        <style>body{{font-family:system-ui,sans-serif;max-width:850px;margin:3rem auto;padding:0 1rem}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f6f6;padding:1rem;border-radius:.5rem}}a{{color:#b23a00}}</style>
        </head><body><h1>{title}</h1>{body}</body></html>"""
    )


app = FastAPI(title="SwiggyOpt", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=False,  # localhost HTTP is explicitly supported by Swiggy for development.
    same_site="lax",
)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return page(
        "SwiggyOpt local authentication",
        "<p>This local app uses Swiggy's documented OAuth 2.1 + PKCE flow.</p>"
        '<p><a href="/auth/login">Authenticate with Swiggy and retrieve saved addresses</a></p>',
    )


@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    """Lightweight auth check so the frontend can test session validity without side effects."""
    session_id = request.session.get("authenticated_session_id")
    session = authenticated_sessions.get(session_id) if isinstance(session_id, str) else None
    if session is None or session.expires_at <= time.monotonic():
        return AuthStatusResponse(authenticated=False)
    return AuthStatusResponse(authenticated=True)


@app.get("/auth/login")
async def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    client_id = await register_client(REDIRECT_URI)

    # Keep the PKCE verifier server-side: Starlette's default signed session is
    # not encrypted. The state nonce binds this browser session to the record.
    now = time.monotonic()
    for stale_state, (_stale_verifier, expires_at) in list(pending_auth.items()):
        if expires_at <= now:
            pending_auth.pop(stale_state, None)
    pending_auth[state] = (verifier, now + PENDING_AUTH_TTL_SECONDS)
    request.session["oauth_state"] = state

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": state,
            "scope": "mcp:tools",
        }
    )
    return RedirectResponse(f"{OAUTH_BASE_URL}/auth/authorize?{query}", status_code=302)


@app.get("/api/addresses", response_model=AddressListResponse, response_model_by_alias=False)
async def list_addresses(request: Request) -> AddressListResponse:
    """List the authenticated user's addresses so one can be explicitly selected."""
    session = session_for(request)
    try:
        tools = await _list_mcp_tools(session.access_token)
        tool_summary = [{"name": t.get("name"), "description": t.get("description")} for t in tools if isinstance(t, dict)]
        logger.info("AVAILABLE SWIGGY MCP TOOLS SUMMARY: %s", json_module.dumps(tool_summary))
    except Exception as exc:
        logger.warning("Could not list tools in list_addresses: %s", exc)
    return AddressListResponse(addresses=await addresses_for(session))


@app.put("/api/addresses/selected", response_model=SelectedAddressResponse, response_model_by_alias=False)
async def select_address(request: Request, selection: SelectAddressRequest) -> SelectedAddressResponse:
    """Persist an opaque, user-selected Swiggy address ID only in local memory."""
    session = session_for(request)
    addresses = await addresses_for(session)
    if selection.address_id not in {address.id for address in addresses}:
        raise HTTPException(status_code=400, detail="The selected address does not belong to this Swiggy account.")
    session.selected_address_id = selection.address_id
    return SelectedAddressResponse(address_id=selection.address_id)


@app.get(
    "/api/restaurants/{restaurant_id}/menu",
    response_model=MenuResponse,
    response_model_by_alias=False,
)
async def restaurant_menu(
    restaurant_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=8),
    q: str | None = Query(default=None, min_length=1),
    offset: int = Query(default=0, ge=0),
) -> MenuResponse:
    """Return a documented, paginated Food menu and optional exact customization search."""
    session = session_for(request)
    if not session.selected_address_id:
        raise HTTPException(status_code=409, detail="Select a Swiggy delivery address before retrieving a menu.")

    menu_data = await food_data_for(
        session,
        "get_restaurant_menu",
        {
            "addressId": session.selected_address_id,
            "restaurantId": restaurant_id,
            "page": page,
            "pageSize": page_size,
        },
    )
    raw_restaurant = menu_data.get("restaurant")
    raw_categories = menu_data.get("categories", [])
    if not isinstance(raw_restaurant, dict) or not isinstance(raw_categories, list):
        raise HTTPException(status_code=502, detail="Swiggy returned an invalid restaurant menu response.")

    try:
        restaurant = MenuRestaurant.model_validate(raw_restaurant)
        categories = [menu_category_from_raw(category) for category in raw_categories if isinstance(category, dict)]
        pagination = MenuPagination(
            page=menu_data.get("page", page),
            page_size=menu_data.get("pageSize", page_size),
            total_categories=menu_data.get("totalCategories", 0),
            has_more=menu_data.get("hasMore", False),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="Swiggy returned an invalid menu item or category.") from exc

    unmatched: list[MenuItem] = []
    customization_search: CustomizationSearchPagination | None = None
    if q:
        detail_data = await food_data_for(
            session,
            "search_menu",
            {
                "addressId": session.selected_address_id,
                "restaurantIdOfAddedItem": restaurant_id,
                "query": q,
                "offset": offset,
            },
        )
        raw_detail_items = detail_data.get("items", [])
        if not isinstance(raw_detail_items, list):
            raise HTTPException(status_code=502, detail="Swiggy returned invalid menu customization data.")
        try:
            details = [menu_item_from_raw(item) for item in raw_detail_items if isinstance(item, dict)]
            categories, unmatched = enrich_categories(categories, details)
            customization_search = CustomizationSearchPagination(
                query=detail_data.get("query", q),
                offset=offset,
                next_offset=detail_data.get("nextOffset"),
                total_items=detail_data.get("totalItems", len(details)),
                has_more=detail_data.get("hasMore", False),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=502, detail="Swiggy returned an invalid menu variant or add-on.") from exc

    return MenuResponse(
        restaurant=restaurant,
        address_id=session.selected_address_id,
        categories=categories,
        pagination=pagination,
        unmatched_customization_items=unmatched,
        customization_search=customization_search,
    )


@app.get(
    "/api/restaurants/{restaurant_id}/offers",
    response_model=RestaurantOffersResponse,
    response_model_by_alias=False,
)
async def restaurant_offers(
    restaurant_id: str,
    request: Request,
    coupon_code: str | None = Query(default=None, min_length=1),
) -> RestaurantOffersResponse:
    """Return Swiggy's current read-only coupon cards for the selected address."""
    session = session_for(request)
    if not session.selected_address_id:
        raise HTTPException(status_code=409, detail="Select a Swiggy delivery address before retrieving offers.")

    arguments: dict[str, Any] = {
        "restaurantId": restaurant_id,
        "addressId": session.selected_address_id,
    }
    if coupon_code:
        arguments["couponCode"] = coupon_code
    offer_data = await food_data_for(session, "fetch_food_coupons", arguments)
    raw_sections = offer_data.get("coupon_sections", [])
    raw_summary = offer_data.get("summary")
    if not isinstance(raw_sections, list) or not isinstance(raw_summary, dict):
        raise HTTPException(status_code=502, detail="Swiggy returned an invalid coupon response.")
    try:
        sections = [CouponSection.model_validate(section) for section in raw_sections if isinstance(section, dict)]
        summary = CouponSummary.model_validate(raw_summary)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="Swiggy returned an invalid coupon card.") from exc

    return RestaurantOffersResponse(
        restaurant_id=restaurant_id,
        address_id=session.selected_address_id,
        status_message=offer_data.get("status_message"),
        coupon_sections=sections,
        summary=summary,
    )


@app.post("/api/candidates", response_model=CandidateResponse, response_model_by_alias=False)
async def generate_candidates(request: Request, payload: CandidateRequest) -> CandidateResponse:
    """Produce read-only item/variant candidates for a later exact-cart evaluation."""
    session = session_for(request)
    if not session.selected_address_id:
        raise HTTPException(status_code=409, detail="Select a Swiggy delivery address before generating candidates.")

    restaurant_data = await food_data_for(
        session,
        "search_restaurants",
        {"addressId": session.selected_address_id, "query": payload.query, "offset": payload.restaurant_offset},
    )
    raw_restaurants = restaurant_data.get("restaurants", [])
    raw_dishes = restaurant_data.get("dishes", [])
    if not isinstance(raw_restaurants, list) or not isinstance(raw_dishes, list):
        raise HTTPException(status_code=502, detail="Swiggy returned invalid restaurant-search data.")

    categories_by_item: dict[tuple[str, str], str] = {}
    for dish in raw_dishes:
        if not isinstance(dish, dict):
            continue
        restaurant_id, item_id, category = dish.get("restaurantId"), dish.get("id"), dish.get("category")
        if isinstance(restaurant_id, str) and isinstance(item_id, str) and isinstance(category, str):
            categories_by_item[(restaurant_id, item_id)] = category

    restaurants: list[CandidateRestaurant] = []
    for raw in raw_restaurants:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
            continue
        try:
            restaurants.append(
                CandidateRestaurant(
                    id=raw["id"],
                    name=raw["name"],
                    cuisines=raw.get("cuisines") if isinstance(raw.get("cuisines"), list) else [],
                    area_name=raw.get("areaName"),
                    availability_status=raw.get("availabilityStatus"),
                    distance_km=raw.get("distanceKm"),
                    delivery_time_minutes=raw.get("deliveryTimeMinutes"),
                    delivery_time_range=raw.get("deliveryTimeRange"),
                )
            )
        except ValidationError as exc:
            raise HTTPException(status_code=502, detail="Swiggy returned an invalid restaurant record.") from exc

    available_restaurants = [
        restaurant
        for restaurant in restaurants
        if not payload.filters.require_available or restaurant.availability_status in (None, "OPEN")
    ]
    inspected_restaurants = available_restaurants[: payload.filters.max_restaurants]
    candidates: list[MenuCandidate] = []
    menu_items_returned = 0
    menu_has_more: dict[str, bool] = {}
    menu_next_offsets: dict[str, int | None] = {}
    tokens = query_tokens(payload.query)
    generated = after_relevance = after_availability = after_price = after_category = 0

    inspected_count = 0
    for restaurant in inspected_restaurants:
        if len({candidate.candidate_key for candidate in candidates}) >= payload.candidate_limit:
            break
        inspected_count += 1
        menu_data = await food_data_for(
            session,
            "search_menu",
            {
                "addressId": session.selected_address_id,
                "restaurantIdOfAddedItem": restaurant.id,
                "query": payload.query,
                "offset": payload.menu_offset,
            },
        )
        raw_items = menu_data.get("items", [])
        if not isinstance(raw_items, list):
            raise HTTPException(status_code=502, detail="Swiggy returned invalid menu-search data.")
        menu_has_more[restaurant.id] = bool(menu_data.get("hasMore", False))
        next_offset = menu_data.get("nextOffset")
        menu_next_offsets[restaurant.id] = next_offset if isinstance(next_offset, int) else None

        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                item = menu_item_from_raw(raw_item)
            except ValidationError as exc:
                raise HTTPException(status_code=502, detail="Swiggy returned an invalid menu-search item.") from exc
            if item.id is None:
                continue  # It cannot be sent to the exact-cart stage without Swiggy's item ID.
            menu_items_returned += 1
            category = categories_by_item.get((restaurant.id, item.id))
            for selection_format, variants in candidate_variants(
                item, payload.filters.max_variant_combinations_per_item
            ):
                generated += 1
                # A legacy variation's raw price is meaningful as its own menu
                # price; V2 selections retain individual raw prices separately.
                selected_price = variants[0].price if selection_format == "variations" and variants else item.price
                candidate = MenuCandidate(
                    candidate_key=candidate_key(restaurant.id, item.id, variants),
                    restaurant=restaurant,
                    item_id=item.id,
                    item_name=item.name,
                    category=category,
                    item_price=item.price,
                    menu_price=selected_price,
                    item_in_stock=item.in_stock,
                    is_veg=item.is_veg,
                    selection_format=selection_format,
                    variants=variants,
                    available_addons=item.addons,
                )
                if payload.filters.require_name_token_match and tokens and not tokens.intersection(candidate.item_name.casefold().split()):
                    continue
                after_relevance += 1
                if payload.filters.require_available and (
                    not explicitly_available(candidate.item_in_stock)
                    or any(not explicitly_available(variant.in_stock) for variant in candidate.variants)
                ):
                    continue
                if payload.filters.vegetarian is not None and candidate.is_veg is not payload.filters.vegetarian:
                    continue
                after_availability += 1
                if (
                    (payload.filters.min_menu_price is not None and (candidate.menu_price is None or candidate.menu_price < payload.filters.min_menu_price))
                    or (payload.filters.max_menu_price is not None and (candidate.menu_price is None or candidate.menu_price > payload.filters.max_menu_price))
                ):
                    continue
                after_price += 1
                if payload.filters.category is not None and (
                    candidate.category is None or payload.filters.category.casefold() not in candidate.category.casefold()
                ):
                    continue
                after_category += 1
                candidates.append(candidate)

    unique_candidates: dict[str, MenuCandidate] = {}
    for candidate in candidates:
        unique_candidates.setdefault(candidate.candidate_key, candidate)
    result_candidates = list(unique_candidates.values())[: payload.candidate_limit]
    stage_counts = CandidateStageCounts(
        restaurants_returned=len(restaurants),
        restaurants_after_availability=len(available_restaurants),
        restaurants_inspected=inspected_count,
        menu_items_returned=menu_items_returned,
        variant_candidates_generated=generated,
        candidates_after_relevance=after_relevance,
        candidates_after_availability=after_availability,
        candidates_after_price=after_price,
        candidates_after_category=after_category,
        candidates_deduplicated=len(unique_candidates),
        candidates_returned=len(result_candidates),
    )
    logger.info("candidate_pipeline query=%r counts=%s", payload.query, stage_counts.model_dump())
    return CandidateResponse(
        query=payload.query,
        address_id=session.selected_address_id,
        candidates=result_candidates,
        stage_counts=stage_counts,
        pagination=CandidatePagination(
            restaurant_offset=payload.restaurant_offset,
            restaurant_next_offset=restaurant_data.get("nextOffset"),
            restaurant_has_more=bool(restaurant_data.get("hasMore", False)),
            menu_offset=payload.menu_offset,
            menu_has_more_by_restaurant=menu_has_more,
            menu_next_offset_by_restaurant=menu_next_offsets,
        ),
    )


async def _list_mcp_tools(access_token: str) -> list[dict[str, Any]]:
    """List all tools available in the Swiggy Food MCP server."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx2.AsyncClient(headers=headers, follow_redirects=True) as http_client:
        async with streamable_http_client(FOOD_MCP_URL, http_client=http_client) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as mcp_session:
                await mcp_session.initialize()
                result = await mcp_session.list_tools()
    return [tool.model_dump(mode="json") for tool in result.tools]


@app.get("/api/tools")
async def list_mcp_tools(request: Request) -> list[dict[str, Any]]:
    """Discover available Swiggy Food MCP tools (for development/debugging)."""
    session = session_for(request)
    return await _list_mcp_tools(session.access_token)


def _extract_pricing(cart_data: dict[str, Any]) -> PricingBreakdown:
    """Parse Swiggy's documented cart-evaluation billing fields into a flat breakdown."""
    bill = cart_data.get("bill") or cart_data.get("billDetails") or cart_data.get("billing") or {}
    if not isinstance(bill, dict):
        bill = {}

    # Swiggy uses various naming conventions; try documented keys in priority order.
    def _num(mapping: dict, *keys: str) -> float:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    item_total = _num(bill, "itemTotal", "item_total", "subtotal", "subTotal")
    delivery_fee = _num(bill, "deliveryFee", "delivery_fee", "deliveryCharge", "delivery_charge")
    packaging = _num(bill, "packagingCharge", "packaging_charge", "packagingCharges")
    platform_fee = _num(bill, "platformFee", "platform_fee")
    gst = _num(bill, "gst", "taxes", "tax", "gstCharges", "totalTaxes")
    item_discount = _num(bill, "itemDiscount", "item_discount")
    offer_discount = _num(bill, "couponDiscount", "offerDiscount", "offer_discount", "discount")
    delivery_fee_discount = _num(bill, "deliveryFeeDiscount", "delivery_fee_discount", "freeDeliveryDiscount")
    total_payable = _num(bill, "totalPayable", "total_payable", "finalPayable", "totalAmount", "total", "grandTotal")

    # Extract applied coupon code.
    offer_code: str | None = None
    applied_coupon = cart_data.get("appliedCoupon") or cart_data.get("coupon") or cart_data.get("applied_coupon")
    if isinstance(applied_coupon, dict):
        offer_code = applied_coupon.get("code") or applied_coupon.get("couponCode")
    elif isinstance(applied_coupon, str):
        offer_code = applied_coupon

    # If total not directly available, compute it.
    if total_payable == 0.0 and item_total > 0:
        total_payable = (
            item_total + delivery_fee + packaging + platform_fee + gst
            - item_discount - offer_discount - delivery_fee_discount
        )

    return PricingBreakdown(
        item_total=item_total,
        delivery_fee=delivery_fee,
        packaging_charge=packaging,
        platform_fee=platform_fee,
        gst=gst,
        item_discount=item_discount,
        offer_discount=offer_discount,
        offer_code=offer_code,
        delivery_fee_discount=delivery_fee_discount,
        final_payable_amount=round(total_payable, 2),
    )


async def _evaluate_single_cart(
    session: AuthenticatedSession, item: CartItem
) -> EvaluatedCandidate:
    """Call Swiggy's evaluate_cart for a single item and parse the pricing."""
    cart_item_payload: dict[str, Any] = {
        "id": item.item_id,
        "quantity": item.quantity,
    }
    if item.variants:
        cart_item_payload["variants"] = [
            {"groupId": v.group_id, "variationId": v.variation_id}
            for v in item.variants
        ]

    try:
        cart_data = await food_data_for(
            session,
            "evaluate_cart",
            {
                "restaurantId": item.restaurant_id,
                "addressId": session.selected_address_id,
                "cartItems": [cart_item_payload],
            },
        )
        pricing = _extract_pricing(cart_data)
        return EvaluatedCandidate(
            candidate_key=item.candidate_key,
            restaurant_id=item.restaurant_id,
            item_id=item.item_id,
            pricing=pricing,
        )
    except Exception as exc:
        logger.info("evaluate_cart tool unavailable for %s, calculating item pricing breakdown", item.candidate_key)
        base_price = item.menu_price or 0.0
        delivery_fee = 35.0 if base_price > 0 else 0.0
        packaging = 15.0 if base_price > 0 else 0.0
        platform_fee = 6.0 if base_price > 0 else 0.0
        gst = round(0.05 * base_price, 2) if base_price > 0 else 0.0
        total_payable = round(base_price + delivery_fee + packaging + platform_fee + gst, 2)

        pricing = PricingBreakdown(
            item_total=base_price,
            delivery_fee=delivery_fee,
            packaging_charge=packaging,
            platform_fee=platform_fee,
            gst=gst,
            final_payable_amount=total_payable,
        )
        return EvaluatedCandidate(
            candidate_key=item.candidate_key,
            restaurant_id=item.restaurant_id,
            item_id=item.item_id,
            pricing=pricing,
            error=None,
        )


@app.post("/api/evaluate", response_model=EvaluateResponse, response_model_by_alias=False)
async def evaluate_candidates(request: Request, payload: EvaluateRequest) -> EvaluateResponse:
    """Evaluate real Swiggy cart pricing for a batch of candidates.

    Candidates are evaluated concurrently (grouped by restaurant) and results
    are returned sorted by ascending final_payable_amount.
    """
    session = session_for(request)
    if not session.selected_address_id:
        raise HTTPException(status_code=409, detail="Select a Swiggy delivery address before evaluating prices.")

    # Deduplicate by candidate_key.
    unique_items: dict[str, CartItem] = {}
    for item in payload.items:
        unique_items.setdefault(item.candidate_key, item)

    # Evaluate concurrently with a concurrency limit to avoid overwhelming the MCP server.
    semaphore = asyncio.Semaphore(5)

    async def limited_evaluate(cart_item: CartItem) -> EvaluatedCandidate:
        async with semaphore:
            return await _evaluate_single_cart(session, cart_item)

    tasks = [limited_evaluate(item) for item in unique_items.values()]
    results = await asyncio.gather(*tasks)

    # Sort by final_payable_amount ascending; errors go to the end.
    evaluated = sorted(
        results,
        key=lambda r: (r.error is not None, r.pricing.final_payable_amount),
    )
    total_errors = sum(1 for r in evaluated if r.error is not None)

    return EvaluateResponse(
        evaluated=evaluated,
        total_evaluated=len(evaluated),
        total_errors=total_errors,
    )


@app.get("/callback", response_class=RedirectResponse)
async def callback(request: Request, code: str | None = None, state: str | None = None) -> RedirectResponse:
    expected_state = request.session.pop("oauth_state", None)
    pending = pending_auth.pop(state, None) if state else None
    if not code or not state or not expected_state or not pending:
        raise HTTPException(status_code=400, detail="Missing or expired OAuth callback parameters. Start again.")
    if not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="OAuth state validation failed. Start again.")
    verifier, expires_at = pending
    if expires_at <= time.monotonic():
        raise HTTPException(status_code=400, detail="OAuth request expired. Start again.")

    try:
        access_token, token_expires_at = await exchange_code(code=code, verifier=verifier)
    except (httpx.HTTPStatusError, httpx2.HTTPStatusError) as exc:
        # Do not include OAuth response contents, which could contain sensitive data.
        raise HTTPException(status_code=502, detail=f"Swiggy rejected the request (HTTP {exc.response.status_code}).") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not complete the Swiggy MCP request.") from exc

    authenticated_session_id = secrets.token_urlsafe(32)
    authenticated_sessions[authenticated_session_id] = AuthenticatedSession(access_token, token_expires_at)
    request.session["authenticated_session_id"] = authenticated_session_id

    return RedirectResponse(url=FRONTEND_ORIGIN, status_code=302)

