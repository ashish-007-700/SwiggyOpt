"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  Address,
  Candidate,
  CandidateResponse,
  CandidatePagination,
  CartItemPayload,
  EvaluatedCandidate,
  EvaluateResponse,
  swiggyApi,
} from "@/lib/api";
import { PriceBreakdown, rupees } from "./price-breakdown";

/* ─── Merged result: candidate + real pricing ────────────────────── */

type RankedResult = Candidate & {
  evaluated?: EvaluatedCandidate;
};

/* ─── Component state ────────────────────────────────────────────── */

type LoadState =
  | "idle"
  | "loading-addresses"
  | "searching"
  | "evaluating"
  | "auth-required"
  | "error";

/* ═══════════════════════════════════════════════════════════════════ */

export function SearchWorkbench() {
  /* ── form inputs ─────────────────────────────────────────────── */
  const [query, setQuery] = useState("");
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [selectedAddress, setSelectedAddress] = useState("");

  /* ── UI state ────────────────────────────────────────────────── */
  const [state, setState] = useState<LoadState>("loading-addresses");
  const [error, setError] = useState("");

  /* ── data ────────────────────────────────────────────────────── */
  const [candidateResponse, setCandidateResponse] = useState<CandidateResponse | null>(null);
  const [allCandidates, setAllCandidates] = useState<Candidate[]>([]);
  const [evaluateResponse, setEvaluateResponse] = useState<EvaluateResponse | null>(null);
  const [pagination, setPagination] = useState<CandidatePagination | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  /* ── detail modal ────────────────────────────────────────────── */
  const [selected, setSelected] = useState<RankedResult | null>(null);

  /* ── local filters ───────────────────────────────────────────── */
  const [vegetarian, setVegetarian] = useState("all");
  const [maxPrice, setMaxPrice] = useState("");
  const [restaurantFilter, setRestaurantFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [variantFilter, setVariantFilter] = useState("all");

  /* ── progress tracking ───────────────────────────────────────── */
  const [progressStep, setProgressStep] = useState(0);

  /* ── helpers ─────────────────────────────────────────────────── */

  function handleApiError(caught: unknown) {
    if (caught instanceof ApiError && caught.status === 401) {
      setState("auth-required");
    } else {
      setError(messageFor(caught));
      setState("error");
    }
  }

  /* ── address loading ─────────────────────────────────────────── */

  const loadAddresses = useCallback(async () => {
    setState("loading-addresses");
    try {
      const data = await swiggyApi.addresses();
      setAddresses(data.addresses);
      setSelectedAddress((current) => current || data.addresses[0]?.id || "");
      setState("idle");
    } catch (caught) {
      handleApiError(caught);
    }
  }, []);

  useEffect(() => {
    void loadAddresses();
  }, [loadAddresses]);

  /* ── build cart items from candidates ─────────────────────────── */

  function buildCartItems(candidates: Candidate[]): CartItemPayload[] {
    return candidates.map((c) => ({
      candidate_key: c.candidate_key,
      restaurant_id: c.restaurant.id,
      item_id: c.item_id,
      menu_price: c.menu_price,
      quantity: 1,
      variants: c.variants
        .filter((v) => v.id && v.group_id)
        .map((v) => ({ group_id: v.group_id!, variation_id: v.id! })),
    }));
  }

  /* ── search + evaluate ───────────────────────────────────────── */

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || !selectedAddress) return;

    setState("searching");
    setError("");
    setCandidateResponse(null);
    setAllCandidates([]);
    setEvaluateResponse(null);
    setPagination(null);
    setProgressStep(1);

    try {
      /* Step 1: select address */
      await swiggyApi.selectAddress(selectedAddress);

      /* Step 2: generate candidates */
      setProgressStep(2);
      const data = await swiggyApi.candidates({
        query: query.trim(),
        candidate_limit: 30,
        filters: {
          require_available: true,
          vegetarian: vegetarian === "all" ? null : vegetarian === "veg",
          max_menu_price: maxPrice ? Number(maxPrice) : null,
        },
      });

      setCandidateResponse(data);
      setAllCandidates(data.candidates);
      setPagination(data.pagination);

      if (data.candidates.length === 0) {
        setState("idle");
        return;
      }

      /* Step 3: evaluate real pricing */
      setState("evaluating");
      setProgressStep(3);

      const cartItems = buildCartItems(data.candidates);
      const evalResult = await swiggyApi.evaluate(cartItems);
      setEvaluateResponse(evalResult);
      setProgressStep(4);
      setState("idle");
    } catch (caught) {
      handleApiError(caught);
    }
  }

  /* ── load more candidates ────────────────────────────────────── */

  async function loadMore() {
    if (!pagination?.restaurant_has_more || loadingMore || !query.trim()) return;
    setLoadingMore(true);

    try {
      const nextOffset =
        typeof pagination.restaurant_next_offset === "number"
          ? pagination.restaurant_next_offset
          : typeof pagination.restaurant_next_offset === "string"
            ? parseInt(pagination.restaurant_next_offset, 10)
            : pagination.restaurant_offset + 30;

      const data = await swiggyApi.candidates({
        query: query.trim(),
        candidate_limit: 30,
        restaurant_offset: nextOffset,
        filters: {
          require_available: true,
          vegetarian: vegetarian === "all" ? null : vegetarian === "veg",
          max_menu_price: maxPrice ? Number(maxPrice) : null,
        },
      });

      /* Deduplicate by candidate_key */
      const existingKeys = new Set(allCandidates.map((c) => c.candidate_key));
      const newCandidates = data.candidates.filter((c) => !existingKeys.has(c.candidate_key));
      const merged = [...allCandidates, ...newCandidates];
      setAllCandidates(merged);
      setPagination(data.pagination);
      setCandidateResponse((prev) =>
        prev
          ? {
              ...prev,
              candidates: merged,
              stage_counts: {
                ...prev.stage_counts,
                candidates_returned: merged.length,
              },
              pagination: data.pagination,
            }
          : data,
      );

      /* Evaluate new candidates */
      if (newCandidates.length > 0) {
        const newCartItems = buildCartItems(newCandidates);
        const evalResult = await swiggyApi.evaluate(newCartItems);
        setEvaluateResponse((prev) => {
          if (!prev) return evalResult;
          return {
            evaluated: [...prev.evaluated, ...evalResult.evaluated],
            total_evaluated: prev.total_evaluated + evalResult.total_evaluated,
            total_errors: prev.total_errors + evalResult.total_errors,
          };
        });
      }
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setLoadingMore(false);
    }
  }

  /* ── merge candidates with evaluated pricing ─────────────────── */

  const rankedResults: RankedResult[] = useMemo(() => {
    const evalMap = new Map(
      (evaluateResponse?.evaluated ?? []).map((e) => [e.candidate_key, e]),
    );
    const merged: RankedResult[] = allCandidates.map((c) => ({
      ...c,
      evaluated: evalMap.get(c.candidate_key),
    }));

    /* Sort by final_payable_amount ascending; unpriced go to end */
    merged.sort((a, b) => {
      const aPrice = a.evaluated?.pricing?.final_payable_amount;
      const bPrice = b.evaluated?.pricing?.final_payable_amount;
      const aHasPrice = aPrice != null && aPrice > 0 && !a.evaluated?.error;
      const bHasPrice = bPrice != null && bPrice > 0 && !b.evaluated?.error;
      if (aHasPrice && bHasPrice) return aPrice - bPrice;
      if (aHasPrice) return -1;
      if (bHasPrice) return 1;
      return (a.menu_price ?? Infinity) - (b.menu_price ?? Infinity);
    });

    return merged;
  }, [allCandidates, evaluateResponse]);

  /* ── local filters ───────────────────────────────────────────── */

  const filteredResults = useMemo(
    () =>
      rankedResults.filter((c) => {
        const variant = variantLabel(c);
        return (
          (restaurantFilter === "all" || c.restaurant.id === restaurantFilter) &&
          (categoryFilter === "all" || c.category === categoryFilter) &&
          (variantFilter === "all" || variant === variantFilter)
        );
      }),
    [rankedResults, restaurantFilter, categoryFilter, variantFilter],
  );

  const restaurants = unique(
    rankedResults.map((c) => [c.restaurant.id, c.restaurant.name] as [string, string]),
  );
  const categories = unique(
    rankedResults
      .filter((c) => c.category)
      .map((c) => [c.category!, c.category!] as [string, string]),
  );
  const variants = unique(
    rankedResults.map((c) => [variantLabel(c), variantLabel(c)] as [string, string]),
  );

  /* ── render ──────────────────────────────────────────────────── */

  return (
    <>
      <main className="mx-auto min-h-screen max-w-6xl px-4 py-8 sm:px-8 sm:py-12">
        {/* header */}
        <header className="mb-10 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-2xl bg-orange-600 text-xl shadow-lg shadow-orange-200">
              ₹
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">SwiggyOpt</h1>
              <p className="text-xs text-stone-500">Real checkout-price comparison</p>
            </div>
          </div>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
            Live — FastAPI connected
          </span>
        </header>

        {/* hero search */}
        <section className="rounded-3xl bg-stone-950 p-5 text-white shadow-xl sm:p-8">
          <p className="text-sm font-semibold text-orange-300">Find what you want to eat</p>
          <h2 className="mt-2 max-w-xl text-3xl font-bold tracking-tight sm:text-4xl">
            Compare nearby food options by real final bill.
          </h2>
          <form onSubmit={submit} className="mt-7 grid gap-3 md:grid-cols-[1fr_280px_auto]">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search biryani, pizza, burger…"
              className="min-h-12 rounded-xl border border-white/15 bg-white px-4 text-stone-950 outline-none ring-orange-400 focus:ring-2"
            />
            <select
              value={selectedAddress}
              onChange={(e) => setSelectedAddress(e.target.value)}
              className="min-h-12 rounded-xl border border-white/15 bg-white px-3 text-stone-950 outline-none ring-orange-400 focus:ring-2"
              disabled={state === "loading-addresses"}
            >
              {addresses.length ? (
                addresses.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.address_tag || a.address_category || "Saved address"}
                    {a.address_line ? ` — ${a.address_line}` : ""}
                  </option>
                ))
              ) : (
                <option>Choose an address</option>
              )}
            </select>
            <button
              disabled={
                state === "searching" ||
                state === "evaluating" ||
                !query.trim() ||
                !selectedAddress
              }
              className="min-h-12 rounded-xl bg-orange-500 px-6 font-bold text-white transition hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-stone-600"
            >
              {state === "searching"
                ? "Searching…"
                : state === "evaluating"
                  ? "Evaluating…"
                  : "Search"}
            </button>
          </form>
          <div className="mt-4 flex flex-wrap gap-2 text-sm text-stone-300">
            Try:{" "}
            {["Biryani", "Pizza", "Burger"].map((example) => (
              <button
                key={example}
                onClick={() => setQuery(example)}
                className="rounded-full border border-white/20 px-3 py-1 hover:bg-white/10"
              >
                {example}
              </button>
            ))}
          </div>
        </section>

        {/* auth required */}
        {state === "auth-required" && <AuthRequired onAuthenticated={loadAddresses} />}

        {/* error */}
        {state === "error" && (
          <Notice title="API failure" text={error} action="Try again" onAction={loadAddresses} />
        )}

        {/* progress */}
        {(state === "searching" || state === "evaluating") && (
          <Progress step={progressStep} state={state} />
        )}

        {/* results */}
        {state === "idle" && allCandidates.length > 0 && (
          <section className="mt-8">
            <Filters
              vegetarian={vegetarian}
              setVegetarian={setVegetarian}
              maxPrice={maxPrice}
              setMaxPrice={setMaxPrice}
              restaurant={restaurantFilter}
              setRestaurant={setRestaurantFilter}
              restaurants={restaurants}
              category={categoryFilter}
              setCategory={setCategoryFilter}
              categories={categories}
              variant={variantFilter}
              setVariant={setVariantFilter}
              variants={variants}
            />
            <Results
              results={filteredResults}
              counts={candidateResponse?.stage_counts ?? {}}
              evalResponse={evaluateResponse}
              onSelect={setSelected}
            />
            {pagination?.restaurant_has_more && (
              <div className="mt-6 flex justify-center">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="rounded-xl border border-stone-300 bg-white px-6 py-3 text-sm font-semibold shadow-sm transition hover:border-orange-400 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loadingMore ? "Loading more…" : "Load more restaurants"}
                </button>
              </div>
            )}
          </section>
        )}

        {/* empty state after search */}
        {state === "idle" && candidateResponse && allCandidates.length === 0 && (
          <section className="mt-8 rounded-2xl border border-stone-200 bg-white p-8 text-center">
            <h2 className="font-bold">No results</h2>
            <p className="mt-1 text-sm text-stone-500">
              Try a broader query or adjust your filters.
            </p>
          </section>
        )}
      </main>

      {/* price breakdown modal */}
      {selected && (
        <PriceBreakdown
          candidate={selected}
          evaluated={selected.evaluated}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* Sub-components                                                     */
/* ═══════════════════════════════════════════════════════════════════ */

function Progress({ step, state }: { step: number; state: string }) {
  const steps = [
    { label: "Selecting address", description: "Setting delivery location" },
    { label: "Searching restaurants", description: "Querying Swiggy for matches" },
    { label: "Evaluating pricing", description: "Getting real cart prices from Swiggy" },
    { label: "Ranking results", description: "Sorting by final payable amount" },
  ];
  return (
    <section className="mt-8 rounded-3xl border border-orange-200 bg-white p-6">
      <h2 className="font-bold">
        {state === "evaluating" ? "Evaluating real Swiggy prices…" : "Building candidate set…"}
      </h2>
      <ol className="mt-4 grid gap-3 text-sm sm:grid-cols-4">
        {steps.map((s, i) => {
          const stepNum = i + 1;
          const active = stepNum === step;
          const done = stepNum < step;
          return (
            <li
              key={s.label}
              className={`rounded-xl p-3 transition-all ${
                done
                  ? "bg-emerald-100 text-emerald-900"
                  : active
                    ? "bg-orange-100 text-orange-900 animate-pulse"
                    : "bg-stone-100 text-stone-400"
              }`}
            >
              <b>{stepNum}</b>
              <br />
              {s.label}
              <small className="mt-1 block">{done ? "✓ Done" : active ? "In progress…" : s.description}</small>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function AuthRequired({ onAuthenticated }: { onAuthenticated: () => void }) {
  return (
    <Notice
      title="Swiggy authentication required"
      text="Authenticate with Swiggy in the local backend, then return here and refresh your addresses."
      action="Authenticate with Swiggy"
      onAction={() => window.location.assign(swiggyApi.loginUrl)}
      secondaryAction="Refresh addresses"
      onSecondaryAction={onAuthenticated}
    />
  );
}

function Notice({
  title,
  text,
  action,
  onAction,
  secondaryAction,
  onSecondaryAction,
}: {
  title: string;
  text: string;
  action: string;
  onAction: () => void;
  secondaryAction?: string;
  onSecondaryAction?: () => void;
}) {
  return (
    <section className="mt-8 rounded-2xl border border-amber-200 bg-amber-50 p-5">
      <h2 className="font-bold">{title}</h2>
      <p className="mt-1 text-sm text-stone-600">{text}</p>
      <div className="mt-4 flex gap-3">
        <button
          onClick={onAction}
          className="rounded-lg bg-stone-950 px-4 py-2 text-sm font-semibold text-white"
        >
          {action}
        </button>
        {secondaryAction && (
          <button
            onClick={onSecondaryAction}
            className="rounded-lg border border-stone-300 px-4 py-2 text-sm font-semibold"
          >
            {secondaryAction}
          </button>
        )}
      </div>
    </section>
  );
}

function Filters(props: {
  vegetarian: string;
  setVegetarian: (v: string) => void;
  maxPrice: string;
  setMaxPrice: (v: string) => void;
  restaurant: string;
  setRestaurant: (v: string) => void;
  restaurants: [string, string][];
  category: string;
  setCategory: (v: string) => void;
  categories: [string, string][];
  variant: string;
  setVariant: (v: string) => void;
  variants: [string, string][];
}) {
  return (
    <div className="mb-5 grid gap-3 rounded-2xl border border-stone-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-5">
      <Select
        label="Diet"
        value={props.vegetarian}
        onChange={props.setVegetarian}
        options={[
          ["all", "All items"],
          ["veg", "Vegetarian"],
          ["non-veg", "Non-vegetarian"],
        ]}
      />
      <label className="text-xs font-semibold text-stone-600">
        Max menu price
        <input
          value={props.maxPrice}
          inputMode="numeric"
          onChange={(e) => props.setMaxPrice(e.target.value)}
          placeholder="No limit"
          className="mt-1 block w-full rounded-lg border border-stone-200 px-3 py-2 text-sm"
        />
      </label>
      <Select
        label="Restaurant"
        value={props.restaurant}
        onChange={props.setRestaurant}
        options={[["all", "All restaurants"], ...props.restaurants]}
      />
      <Select
        label="Category"
        value={props.category}
        onChange={props.setCategory}
        options={[["all", "All categories"], ...props.categories]}
      />
      <Select
        label="Variant / size"
        value={props.variant}
        onChange={props.setVariant}
        options={[["all", "All variants"], ...props.variants]}
      />
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="text-xs font-semibold text-stone-600">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 block w-full rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-900"
      >
        {options.map(([id, name]) => (
          <option key={id} value={id}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Results({
  results,
  counts,
  evalResponse,
  onSelect,
}: {
  results: RankedResult[];
  counts: Record<string, number>;
  evalResponse: EvaluateResponse | null;
  onSelect: (r: RankedResult) => void;
}) {
  if (!results.length) {
    return (
      <section className="rounded-2xl border border-stone-200 bg-white p-8 text-center">
        <h2 className="font-bold">No results</h2>
        <p className="mt-1 text-sm text-stone-500">Try a broader query or adjust your filters.</p>
      </section>
    );
  }

  const priced = results.filter((r) => r.evaluated && !r.evaluated.error);

  return (
    <>
      {/* summary bar */}
      <div className="mb-4 flex items-end justify-between">
        <div>
          <p className="text-sm font-semibold text-orange-700">
            {results.length} results · {priced.length} priced
          </p>
          <h2 className="text-2xl font-bold">
            {priced.length > 0 ? "Ranked by real final price" : "Awaiting pricing data"}
          </h2>
        </div>
        {evalResponse && (
          <p className="max-w-xs text-right text-xs text-stone-500">
            {evalResponse.total_evaluated} evaluated · {evalResponse.total_errors} errors
          </p>
        )}
      </div>

      {/* result cards */}
      <div className="grid gap-3">
        {results.map((r, index) => {
          const pricing = r.evaluated?.pricing;
          const hasPricing = pricing && pricing.final_payable_amount > 0 && !r.evaluated?.error;

          return (
            <button
              onClick={() => onSelect(r)}
              key={r.candidate_key}
              className="grid gap-3 rounded-2xl border border-stone-200 bg-white p-4 text-left shadow-sm transition hover:border-orange-300 hover:shadow-md sm:grid-cols-[auto_1fr_auto] sm:items-center"
            >
              {/* rank badge */}
              <span
                className={`grid size-10 place-items-center rounded-full text-sm font-bold ${
                  index < 3 && hasPricing
                    ? "bg-orange-100 text-orange-700"
                    : "bg-stone-100 text-stone-600"
                }`}
              >
                {index + 1}
              </span>

              {/* item details */}
              <div>
                <p className="font-bold">
                  {r.item_name}{" "}
                  <span className="font-normal text-stone-500">— {variantLabel(r)}</span>
                </p>
                <p className="mt-1 text-sm text-stone-600">
                  {r.restaurant.name}
                  {r.restaurant.area_name ? ` · ${r.restaurant.area_name}` : ""}
                  {r.restaurant.delivery_time_range
                    ? ` · ${r.restaurant.delivery_time_range}`
                    : r.restaurant.delivery_time_minutes
                      ? ` · ${r.restaurant.delivery_time_minutes} min`
                      : ""}
                </p>
                <p className="mt-2 text-xs text-stone-500">
                  {r.category || "Category not returned"} ·{" "}
                  {r.is_veg === true
                    ? "🟢 Vegetarian"
                    : r.is_veg === false
                      ? "🔴 Non-vegetarian"
                      : "Diet not returned"}
                </p>
              </div>

              {/* pricing */}
              <div className="text-left sm:text-right">
                {hasPricing ? (
                  <>
                    <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                      Final price
                    </p>
                    <p className="mt-1 text-lg font-bold text-emerald-700">
                      {rupees(pricing.final_payable_amount)}
                    </p>
                    {pricing.delivery_fee > 0 && (
                      <p className="mt-0.5 text-xs text-stone-500">
                        incl. {rupees(pricing.delivery_fee)} delivery
                      </p>
                    )}
                    {pricing.offer_discount > 0 && (
                      <p className="mt-0.5 text-xs font-semibold text-orange-600">
                        {rupees(pricing.offer_discount)} off
                        {pricing.offer_code ? ` (${pricing.offer_code})` : ""}
                      </p>
                    )}
                  </>
                ) : r.evaluated?.error ? (
                  <>
                    <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                      Menu price
                    </p>
                    <p className="mt-1 text-lg font-bold">
                      {r.menu_price != null ? rupees(r.menu_price) : "—"}
                    </p>
                    <p className="mt-1 text-xs text-red-600">Pricing error</p>
                  </>
                ) : (
                  <>
                    <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                      Menu price
                    </p>
                    <p className="mt-1 text-lg font-bold">
                      {r.menu_price != null ? rupees(r.menu_price) : "—"}
                    </p>
                    <p className="mt-1 text-xs text-amber-600">Final price pending</p>
                  </>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* pipeline stats */}
      <p className="mt-4 text-xs text-stone-500">
        Pipeline: {counts.restaurants_returned ?? "?"} restaurants →{" "}
        {counts.restaurants_inspected ?? "?"} inspected → {counts.candidates_returned ?? "?"}{" "}
        candidates.
      </p>
    </>
  );
}

/* ── Utilities ───────────────────────────────────────────────────── */

function variantLabel(candidate: Candidate) {
  return candidate.variants.map((v) => v.name).filter(Boolean).join(" · ") || "Standard";
}

function unique(values: [string, string][]) {
  return Array.from(new Map<string, string>(values).entries());
}

function messageFor(error: unknown) {
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}
