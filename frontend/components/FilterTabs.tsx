"use client";

/**
 * FilterTabs — top-of-dashboard tag selector.
 *
 * Controlled component: parent owns `value` and is notified via `onChange`.
 * Value `null` = "All" (no tag filter). Anything else is sent to the API
 * as the `tag` query-param of GET /api/wallets.
 *
 * Tags come from the SWT pipeline's classification stage; emoji map below
 * mirrors the user's mental model rather than a backend enum, so keep it in
 * sync with new tags if classify_wallet() grows new categories.
 */

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TAG_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: null, label: "All" },
  { value: "smart_candidate", label: "🧠 Smart" },
  { value: "market_maker", label: "🎯 Market Maker" },
  { value: "proxy_bot", label: "🤖 Proxy Bot" },
  { value: "high_frequency", label: "⚡ High Frequency" },
  { value: "insufficient_data", label: "📉 Insufficient" },
  { value: "data_clipped", label: "⚠️ Clipped" },
];

const ALL_KEY = "__all__";

export interface FilterTabsProps {
  value: string | null;
  onChange: (next: string | null) => void;
}

export function FilterTabs({ value, onChange }: FilterTabsProps) {
  return (
    <Tabs
      value={value ?? ALL_KEY}
      onValueChange={(next) => onChange(next === ALL_KEY ? null : next)}
    >
      <TabsList className="h-auto flex-wrap">
        {TAG_OPTIONS.map((opt) => (
          <TabsTrigger key={opt.value ?? ALL_KEY} value={opt.value ?? ALL_KEY}>
            {opt.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
