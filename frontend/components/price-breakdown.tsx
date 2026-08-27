"use client";

import { Candidate, EvaluatedCandidate } from "@/lib/api";

type Props = {
  candidate: Candidate;
  evaluated?: EvaluatedCandidate;
  onClose: () => void;
};

export function PriceBreakdown({ candidate, evaluated, onClose }: Props) {
  const variant =
    candidate.variants
      .map((v) => v.name)
      .filter(Boolean)
      .join(" · ") || "Standard";

  const pricing = evaluated?.pricing;
  const hasPricing = pricing && pricing.final_payable_amount > 0 && !evaluated?.error;

  return (
    <div
      className="fixed inset-0 z-20 flex items-end bg-stone-950/40 p-3 sm:items-center sm:justify-center"
      onClick={onClose}
    >
      <section
        className="w-full max-w-md rounded-3xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-orange-700">{candidate.restaurant.name}</p>
            <h2 className="mt-1 text-xl font-bold">{candidate.item_name}</h2>
            <p className="mt-1 text-sm text-stone-500">{variant}</p>
          </div>
          <button
            className="rounded-full p-2 text-stone-500 hover:bg-stone-100"
            onClick={onClose}
            aria-label="Close price breakdown"
          >
            ✕
          </button>
        </div>

        {/* pricing breakdown */}
        {hasPricing ? (
          <dl className="mt-6 space-y-3 text-sm">
            <Row label="Item total" value={rupees(pricing.item_total)} />
            {pricing.item_discount > 0 && (
              <Row label="Item discount" value={`−${rupees(pricing.item_discount)}`} discount />
            )}
            <Row label="Delivery fee" value={rupees(pricing.delivery_fee)} />
            {pricing.delivery_fee_discount > 0 && (
              <Row
                label="Delivery fee discount"
                value={`−${rupees(pricing.delivery_fee_discount)}`}
                discount
              />
            )}
            {pricing.packaging_charge > 0 && (
              <Row label="Packaging charges" value={rupees(pricing.packaging_charge)} />
            )}
            {pricing.platform_fee > 0 && (
              <Row label="Platform fee" value={rupees(pricing.platform_fee)} />
            )}
            {pricing.gst > 0 && <Row label="GST & restaurant charges" value={rupees(pricing.gst)} />}
            {pricing.offer_discount > 0 && (
              <Row
                label={`Offer discount${pricing.offer_code ? ` (${pricing.offer_code})` : ""}`}
                value={`−${rupees(pricing.offer_discount)}`}
                discount
              />
            )}
            <Row
              label="Final payable amount"
              value={rupees(pricing.final_payable_amount)}
              emphasis
            />
          </dl>
        ) : evaluated?.error ? (
          <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-4">
            <h3 className="font-semibold text-red-800">Pricing error</h3>
            <p className="mt-1 text-sm leading-6 text-red-700">{evaluated.error}</p>
            {candidate.menu_price != null && (
              <dl className="mt-4 space-y-2 text-sm">
                <Row label="Menu price (unverified)" value={rupees(candidate.menu_price)} />
              </dl>
            )}
          </div>
        ) : (
          <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-4">
            <h3 className="font-semibold">Pricing pending</h3>
            <p className="mt-1 text-sm leading-6 text-stone-600">
              The real checkout price is still being fetched from Swiggy.
            </p>
            {candidate.menu_price != null && (
              <dl className="mt-4 space-y-2 text-sm">
                <Row label="Menu price (unverified)" value={rupees(candidate.menu_price)} />
              </dl>
            )}
          </div>
        )}

        {/* metadata */}
        <div className="mt-5 rounded-xl bg-stone-50 p-3 text-xs text-stone-500">
          <p>
            Restaurant: {candidate.restaurant.name}
            {candidate.restaurant.area_name ? ` · ${candidate.restaurant.area_name}` : ""}
          </p>
          <p className="mt-1">
            {candidate.is_veg === true
              ? "🟢 Vegetarian"
              : candidate.is_veg === false
                ? "🔴 Non-vegetarian"
                : "Diet info not available"}
            {candidate.category ? ` · ${candidate.category}` : ""}
          </p>
          <p className="mt-1 font-mono text-stone-400">Key: {candidate.candidate_key}</p>
        </div>
      </section>
    </div>
  );
}

/* ─── Row helper ─────────────────────────────────────────────────── */

function Row({
  label,
  value,
  emphasis = false,
  discount = false,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  discount?: boolean;
}) {
  return (
    <div
      className={`flex justify-between gap-4 ${
        emphasis
          ? "border-t border-stone-200 pt-3 font-bold text-stone-950"
          : discount
            ? "text-emerald-700"
            : "text-stone-600"
      }`}
    >
      <dt>{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}

/* ─── Currency formatter ─────────────────────────────────────────── */

export function rupees(value: number) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}
