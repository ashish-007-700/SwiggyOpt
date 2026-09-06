"""Public models for authenticated address selection and Food menu discovery."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeliveryAddress(BaseModel):
    """A saved address shape documented for Swiggy's get_addresses tool."""

    id: str
    address_line: str | None = Field(default=None, validation_alias="addressLine")
    phone_number: str | None = Field(default=None, validation_alias="phoneNumber")
    address_category: str | None = Field(default=None, validation_alias="addressCategory")
    address_tag: str | None = Field(default=None, validation_alias="addressTag")


class AddressListResponse(BaseModel):
    addresses: list[DeliveryAddress]


class SelectAddressRequest(BaseModel):
    address_id: str = Field(min_length=1)


class SelectedAddressResponse(BaseModel):
    address_id: str


class MenuRestaurant(BaseModel):
    id: str
    name: str
    city: str | None = None
    area_name: str | None = Field(default=None, validation_alias="areaName")
    cuisines: list[str] = Field(default_factory=list)
    avg_rating: float | None = Field(default=None, validation_alias="avgRating")
    avg_rating_string: str | None = Field(default=None, validation_alias="avgRatingString")
    total_ratings_string: str | None = Field(default=None, validation_alias="totalRatingsString")
    cost_for_two_message: str | None = Field(default=None, validation_alias="costForTwoMessage")
    is_open: bool | None = Field(default=None, validation_alias="isOpen")
    delivery_time: int | None = Field(default=None, validation_alias="deliveryTime")
    sla_string: str | None = Field(default=None, validation_alias="slaString")
    address: str | None = None
    image_url: str | None = Field(default=None, validation_alias="imageUrl")


class VariantChoice(BaseModel):
    """One exact Swiggy variant choice; group_id must travel with id later."""

    id: str | None = None
    name: str | None = None
    price: float | None = None
    is_veg: bool | None = Field(default=None, validation_alias="isVeg")
    group_id: str | None = Field(default=None, validation_alias="groupId")
    is_default: int | None = Field(default=None, validation_alias="default")
    in_stock: int | None = Field(default=None, validation_alias="inStock")


class VariantGroup(BaseModel):
    """The variantsV2 form: each group and choice identity is preserved."""

    group_id: str = Field(validation_alias="groupId")
    name: str
    variations: list[VariantChoice]


class AddonChoice(BaseModel):
    id: str
    name: str
    price: float


class AddonGroup(BaseModel):
    group_id: str = Field(validation_alias="groupId")
    group_name: str = Field(validation_alias="groupName")
    min_addons: int | None = Field(default=None, validation_alias="minAddons")
    max_addons: int | None = Field(default=None, validation_alias="maxAddons")
    max_free_addons: int | None = Field(default=None, validation_alias="maxFreeAddons")
    choices: list[AddonChoice]


class MenuItem(BaseModel):
    """Compact menu item, optionally enriched with exact scoped-search details."""

    id: str | None = None
    name: str
    description: str | None = None
    price: float | None = None
    in_stock: int | None = Field(default=None, validation_alias="inStock")
    is_veg: bool | None = Field(default=None, validation_alias="isVeg")
    is_bestseller: bool | None = Field(default=None, validation_alias="isBestseller")
    rating: str | int | float | None = None
    image_url: str | None = Field(default=None, validation_alias="imageUrl")
    has_variants: bool | None = Field(default=None, validation_alias="hasVariants")
    has_addons: bool | None = Field(default=None, validation_alias="hasAddons")
    # Swiggy returns one of these formats for a given item, never both.
    variations: list[VariantChoice] = Field(default_factory=list)
    variants_v2: list[VariantGroup] = Field(default_factory=list, validation_alias="variantsV2")
    addons: list[AddonGroup] = Field(default_factory=list)


class MenuCategory(BaseModel):
    title: str
    category_id: str | None = Field(default=None, validation_alias="categoryId")
    image_url: str | None = Field(default=None, validation_alias="imageUrl")
    total_items: int | None = Field(default=None, validation_alias="totalItems")
    has_more_items: bool | None = Field(default=None, validation_alias="hasMoreItems")
    items: list[MenuItem] = Field(default_factory=list)
    subcategories: list["MenuCategory"] = Field(default_factory=list)
    total_subcategories: int | None = Field(default=None, validation_alias="totalSubcategories")


class MenuPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=8)
    total_categories: int = Field(ge=0)
    has_more: bool


class CustomizationSearchPagination(BaseModel):
    query: str
    offset: int = Field(ge=0)
    next_offset: int | None = None
    total_items: int = Field(ge=0)
    has_more: bool


class MenuResponse(BaseModel):
    restaurant: MenuRestaurant
    address_id: str
    categories: list[MenuCategory]
    pagination: MenuPagination
    # Scoping by restaurant alone does not make a search result belong to a
    # category, so unmatched IDs remain explicit rather than being guessed.
    unmatched_customization_items: list[MenuItem] = Field(default_factory=list)
    customization_search: CustomizationSearchPagination | None = None


class CouponTerms(BaseModel):
    title: str | None = None
    bullet_texts: list[str] = Field(default_factory=list, validation_alias="bullet_texts")


class FoodCoupon(BaseModel):
    """A coupon card exactly as documented, retaining any future Swiggy fields."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    applicable: bool | None = None
    applicability_status: str | None = Field(default=None, validation_alias="applicabilityStatus")
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    ribbon_text: str | None = Field(default=None, validation_alias="ribbon_text")
    terms_and_conditions: CouponTerms | None = Field(default=None, validation_alias="terms_and_conditions")


class CouponSection(BaseModel):
    title: str | None = None
    type: str | None = None
    coupons: list[FoodCoupon] = Field(default_factory=list)


class CouponSummary(BaseModel):
    total_coupons: int = Field(ge=0)
    applicable_coupons: int = Field(ge=0)
    sections_count: int = Field(ge=0)
    filter_applied: str | None = None


class RestaurantOffersResponse(BaseModel):
    restaurant_id: str
    address_id: str
    status_message: str | None = None
    coupon_sections: list[CouponSection]
    summary: CouponSummary


class CandidateFilters(BaseModel):
    require_available: bool = True
    vegetarian: bool | None = None
    min_menu_price: float | None = Field(default=None, ge=0)
    max_menu_price: float | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, min_length=1)
    require_name_token_match: bool = True
    max_restaurants: int = Field(default=8, ge=1, le=20)
    max_variant_combinations_per_item: int = Field(default=24, ge=1, le=100)


class CandidateRequest(BaseModel):
    query: str = Field(min_length=1)
    candidate_limit: int = Field(default=30, ge=1, le=100)
    restaurant_offset: int = Field(default=0, ge=0)
    menu_offset: int = Field(default=0, ge=0)
    filters: CandidateFilters = Field(default_factory=CandidateFilters)


class CandidateRestaurant(BaseModel):
    id: str
    name: str
    cuisines: list[str] = Field(default_factory=list)
    area_name: str | None = None
    availability_status: str | None = None
    distance_km: float | None = None
    delivery_time_minutes: int | None = None
    delivery_time_range: str | None = None


class CandidateVariant(BaseModel):
    id: str | None = None
    name: str | None = None
    group_id: str | None = None
    price: float | None = None
    in_stock: int | None = None


class MenuCandidate(BaseModel):
    candidate_key: str
    restaurant: CandidateRestaurant
    item_id: str
    item_name: str
    category: str | None = None
    item_price: float | None = None
    menu_price: float | None = None
    item_in_stock: int | None = None
    is_veg: bool | None = None
    selection_format: str
    variants: list[CandidateVariant] = Field(default_factory=list)
    available_addons: list[AddonGroup] = Field(default_factory=list)


class CandidateStageCounts(BaseModel):
    restaurants_returned: int
    restaurants_after_availability: int
    restaurants_inspected: int
    menu_items_returned: int
    variant_candidates_generated: int
    candidates_after_relevance: int
    candidates_after_availability: int
    candidates_after_price: int
    candidates_after_category: int
    candidates_deduplicated: int
    candidates_returned: int


class CandidatePagination(BaseModel):
    restaurant_offset: int
    restaurant_next_offset: str | int | None = None
    restaurant_has_more: bool
    menu_offset: int
    menu_has_more_by_restaurant: dict[str, bool] = Field(default_factory=dict)
    menu_next_offset_by_restaurant: dict[str, int | None] = Field(default_factory=dict)


class CandidateResponse(BaseModel):
    query: str
    address_id: str
    candidates: list[MenuCandidate]
    stage_counts: CandidateStageCounts
    pagination: CandidatePagination


class CartItemVariant(BaseModel):
    group_id: str
    variation_id: str


class CartItem(BaseModel):
    """One candidate to evaluate through Swiggy's cart pricing."""

    candidate_key: str
    restaurant_id: str
    item_id: str
    menu_price: float | None = None
    quantity: int = Field(default=1, ge=1, le=10)
    variants: list[CartItemVariant] = Field(default_factory=list)


class EvaluateRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=50)


class PricingBreakdown(BaseModel):
    """Every pricing component Swiggy returns for a single-item cart."""

    item_total: float = 0.0
    delivery_fee: float = 0.0
    packaging_charge: float = 0.0
    platform_fee: float = 0.0
    gst: float = 0.0
    item_discount: float = 0.0
    offer_discount: float = 0.0
    offer_code: str | None = None
    delivery_fee_discount: float = 0.0
    final_payable_amount: float = 0.0


class EvaluatedCandidate(BaseModel):
    """A candidate with real Swiggy cart pricing attached."""

    candidate_key: str
    restaurant_id: str
    restaurant_name: str | None = None
    item_id: str
    item_name: str | None = None
    variant_label: str | None = None
    is_veg: bool | None = None
    category: str | None = None
    pricing: PricingBreakdown
    error: str | None = None


class EvaluateResponse(BaseModel):
    evaluated: list[EvaluatedCandidate]
    total_evaluated: int = 0
    total_errors: int = 0


class AuthStatusResponse(BaseModel):
    authenticated: bool
