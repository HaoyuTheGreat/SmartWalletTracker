/**
 * Backend client — thin fetch wrappers around the FastAPI service.
 *
 * Base URL comes from NEXT_PUBLIC_API_URL (set in .env.local locally, in the
 * Vercel UI in prod). The NEXT_PUBLIC_ prefix bakes it into the client bundle.
 *
 * Every wrapper throws on non-2xx so callers can rely on `await` returning a
 * valid typed body. Error messages include the status + endpoint to make
 * Network-tab debugging quick.
 */

import type {
  ChatRequest,
  ChatResponse,
  DashboardStats,
  WalletPage,
} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL;

if (!BASE_URL) {
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. Add it to frontend/.env.local.",
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status} on ${path}: ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function getDashboardStats(): Promise<DashboardStats> {
  return request<DashboardStats>("/api/stats/dashboard");
}

export interface GetWalletsOptions {
  tag?: string;
  sort?:
    | "total_pnl_sol"
    | "win_rate"
    | "total_swaps"
    | "active_days"
    | "classified_at";
  limit?: number;
  offset?: number;
}

export function getWallets(
  opts: GetWalletsOptions = {},
): Promise<WalletPage> {
  const params = new URLSearchParams();
  if (opts.tag) params.set("tag", opts.tag);
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return request<WalletPage>(`/api/wallets${qs ? `?${qs}` : ""}`);
}

export function postChat(req: ChatRequest): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify(req),
  });
}
