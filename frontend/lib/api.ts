/* ─── Swiggy address types ───────────────────────────────────────── */

export type Address = {
  id: string;
  address_line?: string;
  address_tag?: string;
  address_category?: string;
};

/* ─── Candidate types (from /api/candidates) ─────────────────────── */

export type CandidateVariant = {
  id?: string;
  name?: string;
  group_id?: string;
  price?: number;
  in_stock?: number;
};

export type CandidateRestaurant = {
  id: string;
  name: string;
  cuisines: string[];
  area_name?: string;
  availability_status?: string;
  distance_km?: number;
  delivery_time_minutes?: number;
  delivery_time_range?: string;
};

export type Candidate = {
  candidate_key: string;
  restaurant: CandidateRestaurant;
  item_id: string;
  item_name: string;
  category?: string;
  item_price?: number;
  menu_price?: number;
  item_in_stock?: number;
  is_veg?: boolean;
  selection_format: string;
  variants: CandidateVariant[];
};

export type CandidatePagination = {
  restaurant_offset: number;
  restaurant_next_offset?: string | number | null;
  restaurant_has_more: boolean;
  menu_offset: number;
  menu_has_more_by_restaurant: Record<string, boolean>;
  menu_next_offset_by_restaurant: Record<string, number | null>;
};

export type CandidateResponse = {
  query: string;
  address_id: string;
  candidates: Candidate[];
  stage_counts: Record<string, number>;
  pagination: CandidatePagination;
};

/* ─── Cart evaluation types (from /api/evaluate) ─────────────────── */

export type PricingBreakdown = {
  item_total: number;
  delivery_fee: number;
  packaging_charge: number;
  platform_fee: number;
  gst: number;
  item_discount: number;
  offer_discount: number;
  offer_code?: string | null;
  delivery_fee_discount: number;
  final_payable_amount: number;
};

export type EvaluatedCandidate = {
  candidate_key: string;
  restaurant_id: string;
  restaurant_name?: string | null;
  item_id: string;
  item_name?: string | null;
  variant_label?: string | null;
  is_veg?: boolean | null;
  category?: string | null;
  pricing: PricingBreakdown;
  error?: string | null;
};

export type EvaluateResponse = {
  evaluated: EvaluatedCandidate[];
  total_evaluated: number;
  total_errors: number;
};

/* ─── Auth types ─────────────────────────────────────────────────── */

export type AuthStatus = {
  authenticated: boolean;
};

/* ─── API error ──────────────────────────────────────────────────── */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/* ─── Fetch wrapper ──────────────────────────────────────────────── */

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, error?.detail ?? "The SwiggyOpt API request failed.");
  }
  return response.json() as Promise<T>;
}

/* ─── API methods ────────────────────────────────────────────────── */

export type CartItemPayload = {
  candidate_key: string;
  restaurant_id: string;
  item_id: string;
  menu_price?: number;
  quantity?: number;
  variants?: { group_id: string; variation_id: string }[];
};

export const swiggyApi = {
  loginUrl: `${baseUrl}/auth/login`,

  authStatus: () => apiFetch<AuthStatus>("/api/auth/status"),

  addresses: () => apiFetch<{ addresses: Address[] }>("/api/addresses"),

  selectAddress: (addressId: string) =>
    apiFetch("/api/addresses/selected", {
      method: "PUT",
      body: JSON.stringify({ address_id: addressId }),
    }),

  candidates: (body: {
    query: string;
    candidate_limit?: number;
    restaurant_offset?: number;
    menu_offset?: number;
    filters?: Record<string, unknown>;
  }) =>
    apiFetch<CandidateResponse>("/api/candidates", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  evaluate: (items: CartItemPayload[]) =>
    apiFetch<EvaluateResponse>("/api/evaluate", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
};
