"use client";

import { useState } from "react";
import {
  useGlobalLeads,
  useLeadLists,
  useDeleteLead,
  useRestoreLead,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

export default function GlobalLeadsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [listFilter, setListFilter] = useState<string | undefined>();

  const { data: leadsData, isLoading } = useGlobalLeads({
    page,
    per_page: 50,
    search: search || undefined,
    status: statusFilter,
    lead_list_id: listFilter,
  });
  const { data: lists } = useLeadLists();
  const deleteLead = useDeleteLead();
  const restoreLead = useRestoreLead();

  const totalPages = leadsData
    ? Math.ceil(leadsData.total / leadsData.per_page)
    : 1;

  const statuses = [
    "pending",
    "scheduled",
    "connection_requested",
    "connected",
    "completed",
    "skipped",
    "error",
    "removed",
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Lead Library"
        description="All leads across all lists and campaigns"
      />

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Search leads..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          className="max-w-xs"
        />
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={statusFilter ?? ""}
          onChange={(e) => {
            setStatusFilter(e.target.value || undefined);
            setPage(1);
          }}
        >
          <option value="">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={listFilter ?? ""}
          onChange={(e) => {
            setListFilter(e.target.value || undefined);
            setPage(1);
          }}
        >
          <option value="">All lists</option>
          {lists?.map((ll) => (
            <option key={ll.id} value={ll.id}>
              {ll.name}
            </option>
          ))}
        </select>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Leads {leadsData ? `(${leadsData.total})` : ""}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : !leadsData || leadsData.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads found</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 font-medium">Name</th>
                      <th className="pb-2 font-medium">Company</th>
                      <th className="pb-2 font-medium">Title</th>
                      <th className="pb-2 font-medium">Status</th>
                      <th className="pb-2 font-medium">LinkedIn</th>
                      <th className="pb-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leadsData.items.map((lead) => (
                      <tr key={lead.id} className="border-b last:border-0">
                        <td className="py-2">
                          {[lead.first_name, lead.last_name]
                            .filter(Boolean)
                            .join(" ") || "—"}
                        </td>
                        <td className="py-2">{lead.company ?? "—"}</td>
                        <td className="py-2">{lead.title ?? "—"}</td>
                        <td className="py-2">
                          <StatusBadge status={lead.status} />
                        </td>
                        <td className="py-2">
                          <a
                            href={lead.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-500 hover:underline truncate block max-w-[180px]"
                          >
                            {lead.linkedin_url.replace(
                              "https://www.linkedin.com/in/",
                              ""
                            )}
                          </a>
                        </td>
                        <td className="py-2">
                          {lead.status === "removed" ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                restoreLead.mutate(lead.id, {
                                  onSuccess: () =>
                                    toast.success("Lead restored"),
                                  onError: (err) => toast.error(err.message),
                                })
                              }
                            >
                              Restore
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-destructive"
                              onClick={() =>
                                deleteLead.mutate(lead.id, {
                                  onSuccess: () =>
                                    toast.success("Lead removed"),
                                  onError: (err) => toast.error(err.message),
                                })
                              }
                            >
                              Remove
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    Previous
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    Page {page} of {totalPages}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={page >= totalPages}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
