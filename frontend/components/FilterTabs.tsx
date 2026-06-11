"use client";

/**
 * FilterTabs — tag filter as a mono segmented control (quant-terminal style).
 *
 * Controlled component: parent owns `value` and is notified via `onChange`.
 * Value `null` = "All" (no tag filter). Anything else is sent to the API
 * as the `tag` query-param of GET /api/wallets.
 *
 * Tag values come from the SWT pipeline's classification stage — keep this
 * list in sync if classify_wallet() grows new categories.
 */

const TAG_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: null, label: "All" },
  { value: "smart_candidate", label: "Smart" },
  { value: "market_maker", label: "Market Maker" },
  { value: "proxy_bot", label: "Proxy Bot" },
  { value: "high_frequency", label: "High Freq" },
  { value: "insufficient_data", label: "Insufficient" },
  { value: "data_clipped", label: "Clipped" },
];

export interface FilterTabsProps {
  value: string | null;
  onChange: (next: string | null) => void;
}

export function FilterTabs({ value, onChange }: FilterTabsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {TAG_OPTIONS.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.label}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`rounded border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              active
                ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-400"
                : "border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
