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
import { useLeads } from "@/hooks/use-queries";
import { Skeleton } from "@/components/ui/skeleton";

interface LeadsTableProps {
  campaignId: string;
}

// Sentinel value meaning "all active (exclude removed)"
const ALL_ACTIVE = "__all_active__";

export function LeadsTable({ campaignId }: LeadsTableProps) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>(ALL_ACTIVE);

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
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
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
                              ? lead.error_message
                              : `Scheduled for ${new Date(lead.scheduled_at!).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`}
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
                    ? new Date(lead.connection_requested_at).toLocaleDateString()
                    : "—"}
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
