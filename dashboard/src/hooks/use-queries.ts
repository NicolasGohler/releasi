"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

// ── Accounts ──────────────────────────────────────────────────────────────

export function useAccounts(params?: { include_archived?: boolean }) {
  return useQuery({
    queryKey: ["accounts", params],
    queryFn: () => api.fetchAccounts(params),
  });
}

export function useAccount(id: string, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["accounts", id],
    queryFn: () => api.fetchAccount(id),
    enabled: opts?.enabled ?? true,
  });
}

export function useAccountActivity(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "activity"],
    queryFn: () => api.fetchAccountActivity(id),
  });
}

export function useAccountStats(id: string, days = 30) {
  return useQuery({
    queryKey: ["accounts", id, "stats", days],
    queryFn: () => api.fetchAccountStats(id, days),
  });
}

export function useAccountSchedule(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "schedule"],
    queryFn: () => api.fetchAccountSchedule(id),
  });
}

export function useAccountHealth(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "health"],
    queryFn: () => api.fetchAccountHealth(id),
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useUpdateAccount(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateAccount>[1]) =>
      api.updateAccount(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts", id] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useUpdateCookie(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { li_at_cookie: string }) => api.updateCookie(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", id] }),
  });
}

// ── Campaigns ─────────────────────────────────────────────────────────────

export function useCampaigns(params?: { account_id?: string; status?: string; include_archived?: boolean }) {
  return useQuery({
    queryKey: ["campaigns", params],
    queryFn: () => api.fetchCampaigns(params),
  });
}

export function useCampaign(id: string) {
  return useQuery({ queryKey: ["campaigns", id], queryFn: () => api.fetchCampaign(id) });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useUpdateCampaign(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Record<string, unknown>>) => api.updateCampaign(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns", id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useActivateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.activateCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function usePauseCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.pauseCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useCampaignStats(id: string, days = 30, granularity: "day" | "hour" = "day") {
  return useQuery({
    queryKey: ["campaign-stats", id, days, granularity],
    queryFn: () => api.fetchCampaignStats(id, days, granularity),
    enabled: !!id,
  });
}

export function useResetLeads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.resetCampaignLeads,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

// ── Leads ─────────────────────────────────────────────────────────────────

export function useLeads(
  campaignId: string,
  params?: { page?: number; per_page?: number; status?: string; search?: string; excludeRemoved?: boolean; leadListId?: string }
) {
  return useQuery({
    queryKey: ["leads", campaignId, params],
    queryFn: () => api.fetchLeads(campaignId, params),
  });
}

export function useImportCSV(campaignId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, listName }: { file: File; listName?: string }) =>
      api.importCSV(campaignId, file, listName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leads", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
    },
  });
}

// ── Lead Lists ───────────────────────────────────────────────────────────

export function useLeadLists(params?: { include_archived?: boolean }) {
  return useQuery({ queryKey: ["lead-lists", params], queryFn: () => api.fetchLeadLists(params) });
}

export function useLeadList(id: string) {
  return useQuery({
    queryKey: ["lead-lists", id],
    queryFn: () => api.fetchLeadList(id),
  });
}

export function useCreateLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createLeadList,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useDeleteLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteLeadList,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useStartEventImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startEventImport,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useScrapeStatus(listId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["scrape-status", listId],
    queryFn: () => api.fetchScrapeStatus(listId!),
    enabled: !!listId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "running") return 3000;
      if (status === "done" || status === "error") {
        // Refresh the list once scrape settles
        qc.invalidateQueries({ queryKey: ["lead-lists"] });
        return false;
      }
      return 3000;
    },
  });
}

export function useImportCSVToList(listId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.importCSVToList(listId, file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists", listId] });
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
    },
  });
}

export function useLeadListLeads(
  listId: string,
  params?: { page?: number; per_page?: number }
) {
  return useQuery({
    queryKey: ["lead-list-leads", listId, params],
    queryFn: () => api.fetchLeadListLeads(listId, params),
  });
}

export function useAssignListToCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ listId, campaignId }: { listId: string; campaignId: string }) =>
      api.assignListToCampaign(listId, campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useUnassignListFromCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ listId, campaignId }: { listId: string; campaignId: string }) =>
      api.unassignListFromCampaign(listId, campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-lists"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

// ── Global Leads ─────────────────────────────────────────────────────────

export function useGlobalLeads(params?: {
  page?: number;
  per_page?: number;
  lead_list_id?: string;
  campaign_id?: string;
  status?: string;
  search?: string;
}) {
  return useQuery({
    queryKey: ["global-leads", params],
    queryFn: () => api.fetchGlobalLeads(params),
  });
}

export function useDeleteLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useRestoreLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.restoreLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useSkipLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.skipLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

export function useRequeueLead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.requeueLead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-leads"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

// ── Archive ───────────────────────────────────────────────────────────────

export function useArchiveAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useUnarchiveAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useArchiveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useUnarchiveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useArchiveLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.archiveLeadList,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

export function useUnarchiveLeadList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.unarchiveLeadList,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-lists"] }),
  });
}

// ── Activity ──────────────────────────────────────────────────────────────

export function useGlobalActivity() {
  return useQuery({
    queryKey: ["activity"],
    queryFn: () => api.fetchGlobalActivity(),
  });
}
