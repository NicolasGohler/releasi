import type {
  Account,
  Campaign,
  CampaignStats,
  Lead,
  LeadList,
  LeadListDetail,
  LeadPage,
  ActionLog,
  DailyStat,
  ImportResponse,
  ScrapeStatus,
  ScheduleSlot,
  AccountHealth,
} from "./types";

// API calls go to same-origin /api/v1/* — the Next.js route handler at
// src/app/api/v1/[...path]/route.ts proxies them to the backend and attaches
// the BACKEND_API_KEY server-side. No secrets in the browser.

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
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

export const fetchAccounts = (params?: { include_archived?: boolean }) => {
  const qs = params?.include_archived ? "?include_archived=true" : "";
  return apiFetch<Account[]>(`/accounts${qs}`);
};

export const fetchAccount = (id: string) => apiFetch<Account>(`/accounts/${id}`);

export const createAccount = (data: {
  name: string;
  li_at_cookie?: string;
  timezone?: string;
  proxy_host?: string | null;
  proxy_port?: number | null;
  proxy_username?: string | null;
  proxy_password?: string | null;
  proxy_country?: string | null;
}) => apiFetch<Account>("/accounts", { method: "POST", body: JSON.stringify(data) });

// For password edits: omit `proxy_password` entirely to keep the existing
// value. Pass an empty string to clear.
export const updateAccount = (
  id: string,
  data: {
    name?: string;
    timezone?: string;
    daily_limit?: number;
    weekly_limit?: number;
    withdraw_threshold?: number | null;
    proxy_host?: string | null;
    proxy_port?: number | null;
    proxy_username?: string | null;
    proxy_password?: string;
    proxy_country?: string | null;
  }
) =>
  apiFetch<Account>(`/accounts/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const testProxyUnsaved = (data: {
  proxy_host: string;
  proxy_port: number;
  proxy_username?: string | null;
  proxy_password?: string | null;
}) =>
  apiFetch<import("./types").ProxyTestResult>("/accounts/test-proxy", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const testProxyStored = (id: string) =>
  apiFetch<import("./types").ProxyTestResult>(`/accounts/${id}/test-proxy`, {
    method: "POST",
  });

export const updateCookie = (id: string, data: { li_at_cookie: string }) =>
  apiFetch<Account>(`/accounts/${id}/cookie`, { method: "PUT", body: JSON.stringify(data) });

export const startLoginSession = (id: string) =>
  apiFetch<{ novnc_url: string; account_id: string }>(`/accounts/${id}/login-session`, {
    method: "POST",
  });

export const finishLoginSession = (id: string) =>
  apiFetch<{ success: boolean; message: string }>(`/accounts/${id}/login-session/finish`, {
    method: "POST",
  });

export const cancelLoginSession = (id: string) =>
  apiFetch<{ success: boolean; message: string }>(`/accounts/${id}/login-session/cancel`, {
    method: "POST",
  });

export const fetchAccountActivity = (id: string, limit = 500) =>
  apiFetch<ActionLog[]>(`/accounts/${id}/activity?limit=${limit}`);

export const fetchAccountStats = (id: string, days = 30) =>
  apiFetch<DailyStat[]>(`/accounts/${id}/stats?days=${days}`);

export const fetchAccountSchedule = (id: string) =>
  apiFetch<ScheduleSlot[]>(`/accounts/${id}/schedule`);

export const fetchAccountHealth = (id: string) =>
  apiFetch<AccountHealth>(`/accounts/${id}/health`);

// ── Campaigns ─────────────────────────────────────────────────────────────

export const fetchCampaigns = (params?: { account_id?: string; status?: string; include_archived?: boolean }) => {
  const sp = new URLSearchParams();
  if (params?.account_id) sp.set("account_id", params.account_id);
  if (params?.status) sp.set("status", params.status);
  if (params?.include_archived) sp.set("include_archived", "true");
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

export const fetchCampaignStats = (id: string, days = 30, granularity: "day" | "hour" = "day") =>
  apiFetch<CampaignStats>(`/campaigns/${id}/stats?days=${days}&granularity=${granularity}`);

// ── Leads ─────────────────────────────────────────────────────────────────

export const fetchLeads = (
  campaignId: string,
  params?: { page?: number; per_page?: number; status?: string; search?: string; excludeRemoved?: boolean }
) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.status) sp.set("status", params.status);
  if (params?.search) sp.set("search", params.search);
  if (params?.excludeRemoved) sp.set("exclude_removed", "true");
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/campaigns/${campaignId}/leads${qs ? `?${qs}` : ""}`);
};

export const importCSV = async (
  campaignId: string,
  file: File,
  listName?: string
): Promise<ImportResponse> => {
  const formData = new FormData();
  formData.append("file", file);
  if (listName) {
    formData.append("list_name", listName);
  }

  const res = await fetch(`/api/v1/campaigns/${campaignId}/import`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
};

// ── Lead Lists ───────────────────────────────────────────────────────────

export const fetchLeadLists = (params?: { include_archived?: boolean }) => {
  const qs = params?.include_archived ? "?include_archived=true" : "";
  return apiFetch<LeadList[]>(`/lead-lists${qs}`);
};

export const fetchLeadList = (id: string) =>
  apiFetch<LeadListDetail>(`/lead-lists/${id}`);

export const createLeadList = (data: { name: string }) =>
  apiFetch<LeadList>("/lead-lists", { method: "POST", body: JSON.stringify(data) });

export const deleteLeadList = (id: string) =>
  apiFetch<{ ok: boolean }>(`/lead-lists/${id}`, { method: "DELETE" });

export const importCSVToList = async (listId: string, file: File): Promise<ImportResponse> => {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`/api/v1/lead-lists/${listId}/import`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }

  return res.json();
};

export const fetchLeadListLeads = (
  listId: string,
  params?: { page?: number; per_page?: number }
) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/lead-lists/${listId}/leads${qs ? `?${qs}` : ""}`);
};

export const exportLeadListCSV = async (listId: string, filename: string) => {
  const res = await fetch(`/api/v1/lead-lists/${listId}/export`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

export const assignListToCampaign = (listId: string, campaignId: string) =>
  apiFetch<{ leads_added: number }>(`/lead-lists/${listId}/assign`, {
    method: "POST",
    body: JSON.stringify({ campaign_id: campaignId }),
  });

export const unassignListFromCampaign = (listId: string, campaignId: string) =>
  apiFetch<{ leads_removed: number }>(`/lead-lists/${listId}/unassign`, {
    method: "POST",
    body: JSON.stringify({ campaign_id: campaignId }),
  });

// ── Global Leads / Lead Management ──────────────────────────────────────

export const fetchGlobalLeads = (params?: {
  page?: number;
  per_page?: number;
  lead_list_id?: string;
  campaign_id?: string;
  status?: string;
  search?: string;
}) => {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.lead_list_id) sp.set("lead_list_id", params.lead_list_id);
  if (params?.campaign_id) sp.set("campaign_id", params.campaign_id);
  if (params?.status) sp.set("status", params.status);
  if (params?.search) sp.set("search", params.search);
  const qs = sp.toString();
  return apiFetch<LeadPage>(`/leads${qs ? `?${qs}` : ""}`);
};

export const deleteLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}`, { method: "DELETE" });

export const restoreLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/restore`, { method: "POST" });

export const skipLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/skip`, { method: "POST" });

export const requeueLead = (id: string) =>
  apiFetch<Lead>(`/leads/${id}/requeue`, { method: "POST" });

// ── Activity ──────────────────────────────────────────────────────────────

export const fetchGlobalActivity = (limit = 500) =>
  apiFetch<ActionLog[]>(`/activity?limit=${limit}`);

// ── Avatars ──────────────────────────────────────────────────────────────

export const getAvatarUrl = (accountId: string) =>
  `/api/v1/accounts/${accountId}/avatar`;

export const fetchAvatar = (accountId: string) =>
  apiFetch<{ success: boolean }>(`/accounts/${accountId}/fetch-avatar`, { method: "POST" });

export const replanAccount = (id: string) =>
  apiFetch<{ ok: boolean; scheduled: number }>(`/accounts/${id}/replan`, { method: "POST" });

// ── Connection Check ────────────────────────────────────────────────────

export const checkConnection = (accountId: string) =>
  apiFetch<{ valid: boolean; title?: string; has_content?: boolean; url?: string; reason?: string; error?: string; elapsed_ms: number }>(
    `/accounts/${accountId}/check-connection`,
    { method: "POST" }
  );

// ── Event Import ─────────────────────────────────────────────────────────

export const startEventImport = (data: {
  url: string;
  account_id: string;
  list_name?: string;
  limit?: number;
}) =>
  apiFetch<LeadList>("/lead-lists/event-import", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const fetchScrapeStatus = (listId: string) =>
  apiFetch<ScrapeStatus>(`/lead-lists/${listId}/scrape-status`);

// ── Archive ──────────────────────────────────────────────────────────────

export const archiveAccount = (id: string) =>
  apiFetch<Account>(`/accounts/${id}/archive`, { method: "POST" });

export const unarchiveAccount = (id: string) =>
  apiFetch<Account>(`/accounts/${id}/unarchive`, { method: "POST" });

export const archiveCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/archive`, { method: "POST" });

export const unarchiveCampaign = (id: string) =>
  apiFetch<Campaign>(`/campaigns/${id}/unarchive`, { method: "POST" });

export const archiveLeadList = (id: string) =>
  apiFetch<LeadList>(`/lead-lists/${id}/archive`, { method: "POST" });

export const unarchiveLeadList = (id: string) =>
  apiFetch<LeadList>(`/lead-lists/${id}/unarchive`, { method: "POST" });
