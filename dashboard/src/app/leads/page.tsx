"use client";

import { useState } from "react";
import {
  useGlobalLeads,
  useLeadLists,
  useCampaigns,
  useDeleteLead,
  useRestoreLead,
  useSkipLead,
  useRequeueLead,
  useBulkSkipLeads,
  useBulkRemoveLeads,
  useBulkRequeueLeads,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  FastForward, RotateCcw, Trash2, Undo2, Mail, Check,
  ChevronUp, ChevronDown, ChevronsUpDown, X,
} from "lucide-react";

type SortKey = "name" | "company" | "status" | "requested_at" | "created_at";

const STATUS_LABELS: Record<string, string> = {
  connection_requested: "requested",
};

const ERROR_LABELS: Record<string, string> = {
  skipped_manually: "Skipped manually",
  email_required: "Email verification required",
  send_button_disabled: "Send button was disabled by LinkedIn",
  no_connect_button: "No Connect button found on profile",
  pending_request: "Connection request already pending",
  preload_navigation_failed: "Failed to load invitation page",
  no_vanity_name: "Could not extract profile identifier",
  weekly_invitation_limit: "Weekly invitation limit reached",
  profile_not_found: "Profile no longer exists",
};

const SKIP_REASON_OPTIONS = [
  { value: "", label: "All skip reasons" },
  { value: "skipped_manually", label: "Skipped manually" },
  { value: "email_required", label: "Email required" },
  { value: "send_button_disabled", label: "Send button disabled" },
  { value: "no_connect_button", label: "No connect button" },
  { value: "pending_request", label: "Already pending" },
  { value: "weekly_invitation_limit", label: "Weekly limit hit" },
  { value: "profile_not_found", label: "Profile not found" },
  { value: "filter_no_photo", label: "Filter: no photo" },
  { value: "filter_low_connections", label: "Filter: low connections" },
  { value: "filter_open_to_work", label: "Filter: open to work" },
  { value: "already_connected", label: "Already connected" },
];

function formatErrorMessage(msg: string): string {
  return ERROR_LABELS[msg] ?? msg.replace(/_/g, " ");
}

function relativeDate(dateStr: string): { label: string; title: string } {
  const date = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const diffDays = Math.floor((Date.now() - date.getTime()) / 86400000);
  const label =
    diffDays === 0 ? "today" :
    diffDays === 1 ? "yesterday" :
    diffDays < 7 ? `${diffDays}d ago` :
    diffDays < 30 ? `${Math.floor(diffDays / 7)}w ago` :
    `${Math.floor(diffDays / 30)}mo ago`;
  return { label, title: date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) };
}

function EmailCopyButton({ email }: { email: string }) {
  const [copied, setCopied] = useState(false);
  function handleCopy(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    navigator.clipboard.writeText(email).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button onClick={handleCopy} className="ml-1.5 inline-flex items-center text-muted-foreground/60 hover:text-muted-foreground transition-colors">
            {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Mail className="h-3 w-3" />}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">{copied ? "Copied!" : email}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function ActionIcon({ icon, label, onClick, disabled, destructive }: {
  icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean; destructive?: boolean;
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon"
            className={`h-7 w-7 ${destructive ? "text-muted-foreground hover:text-destructive hover:bg-destructive/10" : ""}`}
            onClick={onClick} disabled={disabled}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function SortIcon({ col, sortBy, sortDir }: { col: SortKey; sortBy: SortKey | null; sortDir: "asc" | "desc" }) {
  if (sortBy !== col) return <ChevronsUpDown className="ml-1 h-3 w-3 opacity-40 inline" />;
  return sortDir === "asc"
    ? <ChevronUp className="ml-1 h-3 w-3 inline" />
    : <ChevronDown className="ml-1 h-3 w-3 inline" />;
}

export default function GlobalLeadsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [listFilter, setListFilter] = useState<string | undefined>();
  const [campaignFilter, setCampaignFilter] = useState<string | undefined>();
  const [skipReasonFilter, setSkipReasonFilter] = useState<string>("");
  const [requestedAfter, setRequestedAfter] = useState<string>("");
  const [requestedBefore, setRequestedBefore] = useState<string>("");
  const [sortBy, setSortBy] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: leadsData, isLoading } = useGlobalLeads({
    page, per_page: 50,
    search: search || undefined,
    status: statusFilter,
    lead_list_id: listFilter,
    campaign_id: campaignFilter,
    sort_by: sortBy ?? undefined,
    sort_dir: sortDir,
    requested_after: requestedAfter || undefined,
    requested_before: requestedBefore || undefined,
    skip_reason: skipReasonFilter || undefined,
  });
  const { data: lists } = useLeadLists();
  const { data: campaigns } = useCampaigns();
  const deleteLead = useDeleteLead();
  const restoreLead = useRestoreLead();
  const skipLead = useSkipLead();
  const requeueLead = useRequeueLead();
  const bulkSkip = useBulkSkipLeads();
  const bulkRemove = useBulkRemoveLeads();
  const bulkRequeue = useBulkRequeueLeads();

  const totalPages = leadsData ? Math.ceil(leadsData.total / leadsData.per_page) : 1;
  const statuses = ["pending", "scheduled", "connection_requested", "connected", "completed", "skipped", "error", "removed"];

  function toggleSort(col: SortKey) {
    if (sortBy === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortDir("asc");
    }
    setPage(1);
  }

  const hasActiveFilters = search || statusFilter || listFilter || campaignFilter || skipReasonFilter || requestedAfter || requestedBefore || sortBy;

  function resetFilters() {
    setSearch(""); setStatusFilter(undefined); setListFilter(undefined); setCampaignFilter(undefined);
    setSkipReasonFilter(""); setRequestedAfter(""); setRequestedBefore(""); setSortBy(null); setSortDir("desc"); setPage(1);
    setSelected(new Set());
  }

  function toggleSelect(id: string) {
    setSelected((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }

  function toggleSelectAll() {
    if (!leadsData?.items) return;
    const allIds = leadsData.items.map((l) => l.id);
    if (allIds.every((id) => selected.has(id))) {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.delete(id)); return next; });
    } else {
      setSelected((prev) => { const next = new Set(prev); allIds.forEach((id) => next.add(id)); return next; });
    }
  }

  const selectedIds = Array.from(selected);
  const pageIds = leadsData?.items.map((l) => l.id) ?? [];
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  return (
    <div className="space-y-6">
      <PageHeader title="Lead Library" description="All leads across all lists and campaigns" />

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Search leads..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="max-w-xs"
        />
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={statusFilter ?? ""}
          onChange={(e) => { setStatusFilter(e.target.value || undefined); setPage(1); }}
        >
          <option value="">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>{STATUS_LABELS[s] ?? s.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={listFilter ?? ""}
          onChange={(e) => { setListFilter(e.target.value || undefined); setPage(1); }}
        >
          <option value="">All lists</option>
          {lists?.map((ll) => <option key={ll.id} value={ll.id}>{ll.name}</option>)}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={campaignFilter ?? ""}
          onChange={(e) => { setCampaignFilter(e.target.value || undefined); setPage(1); }}
        >
          <option value="">All campaigns</option>
          {campaigns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={skipReasonFilter}
          onChange={(e) => { setSkipReasonFilter(e.target.value); setPage(1); }}
        >
          {SKIP_REASON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <div className="flex items-center gap-1">
          <Input
            type="date"
            value={requestedAfter}
            onChange={(e) => { setRequestedAfter(e.target.value); setPage(1); }}
            className="h-9 w-36 text-sm"
            title="Requested after"
          />
          <span className="text-muted-foreground text-xs">–</span>
          <Input
            type="date"
            value={requestedBefore}
            onChange={(e) => { setRequestedBefore(e.target.value); setPage(1); }}
            className="h-9 w-36 text-sm"
            title="Requested before"
          />
        </div>
        {hasActiveFilters && (
          <Button variant="ghost" size="sm" onClick={resetFilters} className="gap-1 text-muted-foreground">
            <X className="h-3.5 w-3.5" /> Clear
          </Button>
        )}
      </div>

      {/* Bulk action bar */}
      {selectedIds.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
          <span className="text-sm text-muted-foreground mr-2">{selectedIds.length} selected</span>
          <Button size="sm" variant="outline" onClick={() => {
            bulkSkip.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Skipped ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkSkip.isPending}>
            <FastForward className="mr-1 h-3.5 w-3.5" /> Skip
          </Button>
          <Button size="sm" variant="outline" onClick={() => {
            bulkRequeue.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Re-queued ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkRequeue.isPending}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> Re-queue
          </Button>
          <Button size="sm" variant="outline" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => {
            bulkRemove.mutate(selectedIds, {
              onSuccess: (r) => { toast.success(`Removed ${r.updated} leads`); setSelected(new Set()); },
              onError: (e) => toast.error(e.message),
            });
          }} disabled={bulkRemove.isPending}>
            <Trash2 className="mr-1 h-3.5 w-3.5" /> Remove
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} className="ml-auto text-muted-foreground">
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Leads {leadsData ? `(${leadsData.total})` : ""}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : !leadsData || leadsData.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads found</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 w-8">
                        <input type="checkbox" checked={allPageSelected} onChange={toggleSelectAll} className="h-4 w-4 rounded border-border" />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("name")}>
                        Name <SortIcon col="name" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("company")}>
                        Company <SortIcon col="company" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium">Campaign</th>
                      <th className="pb-2 font-medium">List</th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("status")}>
                        Status <SortIcon col="status" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium cursor-pointer select-none" onClick={() => toggleSort("requested_at")}>
                        Requested <SortIcon col="requested_at" sortBy={sortBy} sortDir={sortDir} />
                      </th>
                      <th className="pb-2 font-medium text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leadsData.items.map((lead) => {
                      const requestedAt = lead.connection_requested_at ? relativeDate(lead.connection_requested_at) : null;
                      const isSelected = selected.has(lead.id);
                      return (
                        <tr key={lead.id} className={`border-b last:border-0 ${isSelected ? "bg-muted/30" : ""}`}>
                          <td className="py-2">
                            <input type="checkbox" checked={isSelected} onChange={() => toggleSelect(lead.id)} className="h-4 w-4 rounded border-border" />
                          </td>
                          <td className="py-2">
                            <div className="flex items-center gap-0.5">
                              <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer" className="font-medium hover:underline">
                                {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                              </a>
                              {lead.email && <EmailCopyButton email={lead.email} />}
                            </div>
                          </td>
                          <td className="py-2 text-muted-foreground max-w-[160px]">
                            <span className="block truncate" title={lead.company ?? undefined}>{lead.company ?? "—"}</span>
                          </td>
                          <td className="py-2 text-muted-foreground">{lead.campaign_name ?? "—"}</td>
                          <td className="py-2 text-muted-foreground max-w-[140px]">
                            <span className="block truncate" title={lead.lead_list_name ?? undefined}>{lead.lead_list_name ?? "—"}</span>
                          </td>
                          <td className="py-2">
                            {lead.error_message || (lead.status === "scheduled" && lead.scheduled_at) ? (
                              <TooltipProvider delayDuration={200}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="cursor-help"><StatusBadge status={lead.status} /></span>
                                  </TooltipTrigger>
                                  <TooltipContent side="top" className="max-w-xs">
                                    <p className="text-xs">
                                      {lead.error_message
                                        ? formatErrorMessage(lead.error_message)
                                        : `Scheduled for ${new Date(lead.scheduled_at! + "Z").toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`}
                                    </p>
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : (
                              <StatusBadge status={lead.status} />
                            )}
                          </td>
                          <td className="py-2 text-muted-foreground text-xs whitespace-nowrap">
                            {requestedAt ? (
                              <TooltipProvider delayDuration={200}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="cursor-default">{requestedAt.label}</span>
                                  </TooltipTrigger>
                                  <TooltipContent side="top" className="text-xs">{requestedAt.title}</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : "—"}
                          </td>
                          <td className="py-2 text-right">
                            <div className="flex items-center justify-end gap-0.5">
                              {(lead.status === "pending" || lead.status === "scheduled") && (
                                <ActionIcon icon={<FastForward className="h-3.5 w-3.5" />} label="Skip"
                                  onClick={() => skipLead.mutate(lead.id, { onSuccess: () => toast.success("Lead skipped"), onError: (e) => toast.error(e.message) })}
                                  disabled={skipLead.isPending} />
                              )}
                              {(lead.status === "error" || lead.status === "withdrawn" || lead.status === "skipped") && (
                                <ActionIcon icon={<RotateCcw className="h-3.5 w-3.5" />} label="Re-queue"
                                  onClick={() => requeueLead.mutate(lead.id, { onSuccess: () => toast.success("Lead re-queued"), onError: (e) => toast.error(e.message) })}
                                  disabled={requeueLead.isPending} />
                              )}
                              {lead.status === "removed" ? (
                                <ActionIcon icon={<Undo2 className="h-3.5 w-3.5" />} label="Restore"
                                  onClick={() => restoreLead.mutate(lead.id, { onSuccess: () => toast.success("Lead restored"), onError: (e) => toast.error(e.message) })}
                                  disabled={restoreLead.isPending} />
                              ) : (
                                <ActionIcon icon={<Trash2 className="h-3.5 w-3.5" />} label="Remove"
                                  onClick={() => deleteLead.mutate(lead.id, { onSuccess: () => toast.success("Lead removed"), onError: (e) => toast.error(e.message) })}
                                  disabled={deleteLead.isPending} destructive />
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
                  <span className="text-sm text-muted-foreground">Page {page} of {totalPages}</span>
                  <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
