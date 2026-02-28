import type {
  Account,
  Campaign,
  Lead,
  LeadPage,
  ActionLog,
  DailyStat,
  ImportResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
}

// ── Accounts ──────────────────────────────────────────────────────────────

export const fetchAccounts = () => apiFetch<Account[]>("/accounts");

export const fetchAccount = (id: string) => apiFetch<Account>(`/accounts/${id}`);

export const createAccount = (data: {
  name: string;
  li_at_cookie: string;
  timezone?: string;
  warmup_enabled?: boolean;
}) => apiFetch<Account>("/accounts", { method: "POST", body: JSON.stringify(data) });

export const updateCookie = (id: string, data: { li_at_cookie: string }) =>
  apiFetch<Account>(`/accounts/${id}/cookie`, { method: "PUT", body: JSON.stringify(data) });

export const fetchAccountActivity = (id: string, limit = 50) =>
  apiFetch<ActionLog[]>(`/accounts/${id}/activity?limit=${limit}`);

export const fetchAccountStats = (id: string, days = 30) =>
  apiFetch<DailyStat[]>(`/accounts/${id}/stats?days=${days}`);

// ── Campaigns ─────────────────────────────────────────────────────────────

export const fetchCampaigns = (params?: { account_id?: string; status?: string }) => {
  const sp = new URLSearchParams();
  if (params?.account_id) sp.set("account_id", params.account_id);
  if (params?.status) sp.set("status", params.status);
  const qs = sp.toString();
  return apiFetch<Campaign[]>(`/campaigns${qs ? `?${qs}` : ""}`);
};

export const fetchCampaign = (id: string) => apiFetch<Campaign>(`/campaigns/${id}`);

export const createCampaign = (data: {
  account_id: string;
  name: string;
  connection_message_template?: string;
}) => apiFetch<Campaign>("/campaigns", { method: "POST", body: JSON.stringify(data) });

export const updateCampaign = (id: string, data: Partial<Campaign>) =>
  apiFetch<Campaign>(`/campaigns/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const activateCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/activate`, { method: "POST" });

export const pauseCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/pause`, { method: "POST" });

export const resetCampaignLeads = (id: string) =>
  apiFetch<{ reset_count: number }>(`/campaigns/${id}/reset-leads`, { method: "POST" });

// ── Leads ─────────────────────────────────────────────────────────────────

export const fetchLeads = (
  campaignId: string,
  params?: { page?: number; per_page?: number; status?: string; search?: string }
) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.status) sp.set("status", params.status);
  if (params?.search) sp.set("search", params.search);
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/campaigns/${campaignId}/leads${qs ? `?${qs}` : ""}`);
};

export const importCSV = async (campaignId: string, file: File): Promise<ImportResponse> => {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_URL}/api/v1/campaigns/${campaignId}/import`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_KEY}` },
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
};

// ── Activity ──────────────────────────────────────────────────────────────

export const fetchGlobalActivity = (limit = 50) =>
  apiFetch<ActionLog[]>(`/activity?limit=${limit}`);
