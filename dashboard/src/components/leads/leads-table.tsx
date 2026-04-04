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

interface LeadsTableProps {
  campaignId: string;
  timezone?: string | null;
}

// Sentinel value meaning "all active (exclude removed)"
const ALL_ACTIVE = "__all_active__";

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

export function LeadsTable({ campaignId, timezone }: LeadsTableProps) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(ALL_ACTIVE);

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
  });

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
      <div className="flex items-center gap-3">
        <Input
          placeholder="Search leads..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          className="max-w-xs"
        />
        <div className="flex gap-1">
          {statuses.map((s) => (
            <Button
              key={s}
              variant={statusFilter === s ? "secondary" : "ghost"}
              size="sm"
              onClick={() => {
                setStatusFilter(s);
                setPage(1);
              }}
            >
              {s === ALL_ACTIVE ? "All" : s.replace(/_/g, " ")}
            </Button>
          ))}
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Company</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Requested</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                  No leads found
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((lead) => (
              <TableRow key={lead.id}>
                <TableCell>
                  <a
                    href={lead.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm font-medium hover:underline"
                  >
                    {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                  </a>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {lead.company || "—"}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {lead.title || "—"}
                </TableCell>
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
                              : `Scheduled for ${new Date(lead.scheduled_at! + "Z").toLocaleString(undefined, { timeZone: timezone ?? undefined, dateStyle: "medium", timeStyle: "short" })}`}
                          </p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : (
                    <StatusBadge status={lead.status} />
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {lead.connection_requested_at
                    ? new Date(lead.connection_requested_at.endsWith("Z") ? lead.connection_requested_at : lead.connection_requested_at + "Z").toLocaleDateString(undefined, { timeZone: timezone ?? undefined })
                    : "—"}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    {(lead.status === "pending" || lead.status === "scheduled") && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => skipLead.mutate(lead.id, {
                          onSuccess: () => toast.success("Lead skipped"),
                          onError: (err) => toast.error(err.message),
                        })}
                        disabled={skipLead.isPending}
                      >
                        Skip
                      </Button>
                    )}
                    {(lead.status === "error" || lead.status === "withdrawn" || lead.status === "skipped") && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => requeueLead.mutate(lead.id, {
                          onSuccess: () => toast.success("Lead re-queued"),
                          onError: (err) => toast.error(err.message),
                        })}
                        disabled={requeueLead.isPending}
                      >
                        Re-queue
                      </Button>
                    )}
                    {lead.status === "removed" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => restoreLead.mutate(lead.id, {
                          onSuccess: () => toast.success("Lead restored"),
                          onError: (err) => toast.error(err.message),
                        })}
                        disabled={restoreLead.isPending}
                      >
                        Restore
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs text-muted-foreground hover:text-destructive"
                        onClick={() => deleteLead.mutate(lead.id, {
                          onSuccess: () => toast.success("Lead removed"),
                          onError: (err) => toast.error(err.message),
                        })}
                        disabled={deleteLead.isPending}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            {data?.total ?? 0} total leads
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              {page} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
