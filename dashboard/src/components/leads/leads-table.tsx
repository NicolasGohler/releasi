"use client";

import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useLeads, useDeleteLead, useRestoreLead, useSkipLead, useRequeueLead } from "@/hooks/use-queries";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { exportCampaignLeadsCSV } from "@/lib/api";
import {
  FastForward,
  RotateCcw,
  Trash2,
  Undo2,
  Mail,
  Check,
  MessageSquare,
} from "lucide-react";

interface LeadsTableProps {
  campaignId: string;
  timezone?: string | null;
  assignedLists?: { id: string; name: string; total_leads: number }[];
  campaignName?: string;
}

const ALL_ACTIVE = "__all_active__";

const STATUS_LABELS: Record<string, string> = {
  connection_requested: "requested",
};

const ERROR_LABELS: Record<string, string> = {
  email_required: "Email verification required — LinkedIn requires their email to connect",
  send_button_disabled: "Send button was disabled by LinkedIn",
  no_connect_button: "No Connect button found on profile",
  pending_request: "Connection request already pending",
  preload_navigation_failed: "Failed to load invitation page",
  no_vanity_name: "Could not extract profile identifier",
  weekly_invitation_limit: "Weekly invitation limit reached",
  profile_not_found: "Profile no longer exists (deleted or URL changed)",
};

function formatErrorMessage(msg: string): string {
  return ERROR_LABELS[msg] ?? msg.replace(/_/g, " ");
}

/** Converts a UTC date string to a compact relative label with an exact-date tooltip. */
function relativeDate(dateStr: string, timezone?: string | null): { label: string; title: string } {
  const date = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  let label: string;
  if (diffDays === 0) label = "today";
  else if (diffDays === 1) label = "yesterday";
  else if (diffDays < 7) label = `${diffDays}d ago`;
  else if (diffDays < 30) label = `${Math.floor(diffDays / 7)}w ago`;
  else label = `${Math.floor(diffDays / 30)}mo ago`;

  const title = date.toLocaleString(undefined, {
    timeZone: timezone ?? undefined,
    dateStyle: "medium",
    timeStyle: "short",
  });

  return { label, title };
}

/** Inline mail icon that copies the email address on click. */
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
          <button
            onClick={handleCopy}
            className="ml-1.5 inline-flex items-center text-muted-foreground/60 hover:text-muted-foreground transition-colors"
          >
            {copied
              ? <Check className="h-3 w-3 text-emerald-500" />
              : <Mail className="h-3 w-3" />}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {copied ? "Copied!" : email}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Icon-only action button with a hover tooltip label. */
function ActionButton({
  icon,
  label,
  onClick,
  disabled,
  destructive,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={`h-7 w-7 ${destructive ? "text-muted-foreground hover:text-destructive hover:bg-destructive/10" : ""}`}
            onClick={onClick}
            disabled={disabled}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function LeadsTable({ campaignId, timezone, assignedLists, campaignName }: LeadsTableProps) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(ALL_ACTIVE);
  const [listFilter, setListFilter] = useState<string>("");
  const [exporting, setExporting] = useState(false);

  const skipLead = useSkipLead();
  const requeueLead = useRequeueLead();
  const deleteLead = useDeleteLead();
  const restoreLead = useRestoreLead();

  const isAllActive = statusFilter === ALL_ACTIVE;
  const { data, isLoading } = useLeads(campaignId, {
    page,
    per_page: 25,
    status: isAllActive ? undefined : statusFilter,
    search: search || undefined,
    excludeRemoved: isAllActive,
    leadListId: listFilter || undefined,
  });

  async function handleExport() {
    setExporting(true);
    try {
      const safeName = (campaignName || "campaign").replace(/[^\w-]+/g, "_");
      const today = new Date().toISOString().slice(0, 10);
      await exportCampaignLeadsCSV(campaignId, `${safeName}_leads_${today}.csv`, {
        status: isAllActive ? undefined : statusFilter,
        search: search || undefined,
        excludeRemoved: isAllActive,
        leadListId: listFilter || undefined,
      });
      toast.success(`Exported ${data?.total ?? 0} leads`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  const statuses = [
    ALL_ACTIVE,
    "pending",
    "scheduled",
    "connection_requested",
    "connected",
    "error",
    "skipped",
    "invalid",
    "removed",
  ];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 1;

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search name, company, title, URL…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="max-w-xs"
        />
        {assignedLists && assignedLists.length > 0 && (
          <select
            className="h-9 rounded-md border border-border bg-background px-2 text-sm"
            value={listFilter}
            onChange={(e) => { setListFilter(e.target.value); setPage(1); }}
          >
            <option value="">All lists</option>
            {assignedLists.map((ll) => (
              <option key={ll.id} value={ll.id}>{ll.name}</option>
            ))}
          </select>
        )}
        <div className="flex flex-wrap gap-1">
          {statuses.map((s) => (
            <Button
              key={s}
              variant={statusFilter === s ? "secondary" : "ghost"}
              size="sm"
              onClick={() => { setStatusFilter(s); setPage(1); }}
            >
              {s === ALL_ACTIVE ? "All" : (STATUS_LABELS[s] ?? s.replace(/_/g, " "))}
            </Button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleExport}
          disabled={exporting || !data || data.total === 0}
          className="ml-auto"
          title="Download the currently filtered leads as CSV"
        >
          {exporting ? "Exporting…" : `Export CSV${data ? ` (${data.total})` : ""}`}
        </Button>
      </div>

      {/* Table */}
      <div className="rounded-md border max-h-[600px] overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background shadow-[0_1px_0_0_hsl(var(--border))]">
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Company</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Requested</TableHead>
              <TableHead className="w-8 text-center" title="Follow-up sent">
                <MessageSquare className="h-3.5 w-3.5 mx-auto text-muted-foreground" />
              </TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="py-12 text-center">
                  {statusFilter === "connected" ? (
                    <div className="space-y-1">
                      <p className="text-sm text-muted-foreground">No accepted connections yet</p>
                      <p className="text-xs text-muted-foreground/60">
                        The acceptance checker runs daily at 10 AM in the account timezone
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No leads found</p>
                  )}
                </TableCell>
              </TableRow>
            ) : null}

            {data?.items.map((lead) => {
              const requestedAt = lead.connection_requested_at
                ? relativeDate(lead.connection_requested_at, timezone)
                : null;

              return (
                <TableRow key={lead.id}>
                  {/* Name + email copy icon */}
                  <TableCell>
                    <div className="flex items-center gap-0.5">
                      <a
                        href={lead.linkedin_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm font-medium hover:underline"
                      >
                        {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                      </a>
                      {lead.email && <EmailCopyButton email={lead.email} />}
                    </div>
                  </TableCell>

                  {/* Company */}
                  <TableCell className="text-sm text-muted-foreground max-w-[150px]">
                    <span className="block truncate" title={lead.company || undefined}>
                      {lead.company || "—"}
                    </span>
                  </TableCell>

                  {/* Title */}
                  <TableCell className="text-sm text-muted-foreground max-w-[170px]">
                    <span className="block truncate" title={lead.title || undefined}>
                      {lead.title || "—"}
                    </span>
                  </TableCell>

                  {/* Status (with error/schedule tooltip) */}
                  <TableCell>
                    {lead.error_message || (lead.status === "scheduled" && lead.scheduled_at) ? (
                      <TooltipProvider delayDuration={200}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-help">
                              <StatusBadge status={lead.status} />
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="max-w-xs">
                            <p className="text-xs">
                              {lead.error_message
                                ? formatErrorMessage(lead.error_message)
                                : `Scheduled for ${new Date(lead.scheduled_at! + "Z").toLocaleString(undefined, {
                                    timeZone: timezone ?? undefined,
                                    dateStyle: "medium",
                                    timeStyle: "short",
                                  })}`}
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <StatusBadge status={lead.status} />
                    )}
                  </TableCell>

                  {/* Requested date — relative label + exact date on hover */}
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                    {requestedAt ? (
                      <TooltipProvider delayDuration={200}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-default">{requestedAt.label}</span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="text-xs">
                            {requestedAt.title}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : "—"}
                  </TableCell>

                  {/* Follow-up indicator */}
                  <TableCell className="text-center">
                    {lead.followup_sent_at ? (
                      <TooltipProvider delayDuration={200}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex justify-center">
                              <MessageSquare className="h-3.5 w-3.5 text-emerald-500" />
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="text-xs">
                            Follow-up sent {relativeDate(lead.followup_sent_at, timezone).label}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : (
                      <span className="text-muted-foreground/30 text-xs">—</span>
                    )}
                  </TableCell>

                  {/* Actions — icon buttons */}
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-0.5">
                      {(lead.status === "pending" || lead.status === "scheduled") && (
                        <ActionButton
                          icon={<FastForward className="h-3.5 w-3.5" />}
                          label="Skip"
                          onClick={() => skipLead.mutate(lead.id, {
                            onSuccess: () => toast.success("Lead skipped"),
                            onError: (err) => toast.error(err.message),
                          })}
                          disabled={skipLead.isPending}
                        />
                      )}
                      {(lead.status === "error" || lead.status === "withdrawn" || lead.status === "skipped") && (
                        <ActionButton
                          icon={<RotateCcw className="h-3.5 w-3.5" />}
                          label="Re-queue"
                          onClick={() => requeueLead.mutate(lead.id, {
                            onSuccess: () => toast.success("Lead re-queued"),
                            onError: (err) => toast.error(err.message),
                          })}
                          disabled={requeueLead.isPending}
                        />
                      )}
                      {lead.status === "removed" ? (
                        <ActionButton
                          icon={<Undo2 className="h-3.5 w-3.5" />}
                          label="Restore"
                          onClick={() => restoreLead.mutate(lead.id, {
                            onSuccess: () => toast.success("Lead restored"),
                            onError: (err) => toast.error(err.message),
                          })}
                          disabled={restoreLead.isPending}
                        />
                      ) : (
                        <ActionButton
                          icon={<Trash2 className="h-3.5 w-3.5" />}
                          label="Remove"
                          onClick={() => deleteLead.mutate(lead.id, {
                            onSuccess: () => toast.success("Lead removed"),
                            onError: (err) => toast.error(err.message),
                          })}
                          disabled={deleteLead.isPending}
                          destructive
                        />
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            {data?.total ?? 0} total leads
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
