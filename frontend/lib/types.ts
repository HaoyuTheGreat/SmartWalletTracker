/**
 * TypeScript mirrors of api/schemas.py (Pydantic models).
 *
 * Keep these in sync with the backend manually for now — if the API grows,
 * we can codegen from the OpenAPI spec at /openapi.json instead.
 *
 * `datetime` fields arrive as ISO-8601 strings over JSON (FastAPI serializes
 * datetime → str), so they're typed `string` here, not `Date`.
 */

export interface DashboardStats {
  wallets_tracked: number;
  wallets_classified: number;
  smart_candidates: number;
  total_swaps: number;
  unique_signatures: number;
  raw_data_size_mb: number | null;
  latest_classification_at: string | null;
  latest_swap_at: string | null;
}

export interface WalletSummary {
  wallet_id: string;
  address: string;
  tags: string[];
  win_rate: number | null;
  total_pnl_sol: number | null;
  total_swaps: number | null;
  classified_at: string | null;
}

export interface WalletPage {
  rows: WalletSummary[];
  total: number;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  messages: Message[];
}

export interface ChatResponse {
  message: Message;
  iterations: number;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}
