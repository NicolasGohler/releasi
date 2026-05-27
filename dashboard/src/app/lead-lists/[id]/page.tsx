"use client";

import { use, useState, useCallback, useTransition } from "react";
import { Mail, Check } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useLeadList,
  useLeadListLeads,
  useImportCSVToList,
  useCampaigns,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useDropzone } from "react-dropzone";
import { exportLeadListCSV } from "@/lib/api";

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

export default function LeadListDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: list, isLoading } = useLeadList(id);
  const [page, setPage] = useState(1);
  const { data: leadsData } = useLeadListLeads(id, { page, per_page: 50 });
  const importCSV = useImportCSVToList(id);
  const { data: campaigns } = useCampaigns();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const [selectedCampaign, setSelectedCampaign] = useState("");
  const [isExporting, startExport] = useTransition();

  const onDrop = useCallback(
    (files: File[]) => {
      const file = files[0];
      if (!file) return;
      importCSV.mutate(file, {
        onSuccess: (data) =>
          toast.success(
            `Imported ${data.imported} leads (${data.duplicates_skipped} duplicates skipped)`
          ),
        onError: (err) => toast.error(`Import failed: ${err.message}`),
      });
    },
    [importCSV]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"] },
    maxFiles: 1,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (!list) {
    return <p className="text-muted-foreground">Lead list not found</p>;
  }

  const assignedIds = new Set(list.campaigns.map((c) => c.id));
  const availableCampaigns =
    campaigns?.filter((c) => !assignedIds.has(c.id)) ?? [];

  const totalPages = leadsData ? Math.ceil(leadsData.total / leadsData.per_page) : 1;

  const stats = list.stats;
  const lastImported = new Date(list.updated_at);
  const lastImportedLabel = lastImported.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

  return (
    <div className="space-y-6">
      <PageHeader title={list.name}>
        <span className="text-sm text-muted-foreground">
          {list.total_leads} leads · Last imported {lastImportedLabel}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={isExporting || list.total_leads === 0}
          onClick={() =>
            startExport(async () => {
              try {
                await exportLeadListCSV(id, `${list.name}.csv`);
              } catch (err) {
                toast.error("Export failed");
              }
            })
          }
        >
          {isExporting ? "Exporting..." : "Export CSV"}
        </Button>
      </PageHeader>

      {/* Quality stat cards */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            {
              label: "Acceptance Rate",
              value: stats.acceptance_rate,
              sub: `${Math.round((stats.acceptance_rate / 100) * stats.total)} connected`,
              color: stats.acceptance_rate >= 30 ? "text-emerald-500" : stats.acceptance_rate >= 15 ? "text-amber-500" : "text-red-400",
            },
            {
              label: "Email Coverage",
              value: stats.email_coverage,
              sub: `${Math.round((stats.email_coverage / 100) * stats.total)} leads`,
              color: stats.email_coverage >= 50 ? "text-emerald-500" : stats.email_coverage >= 20 ? "text-amber-500" : "text-muted-foreground",
            },
            {
              label: "Telegram Coverage",
              value: stats.tg_coverage,
              sub: stats.tg_contacted_rate > 0 ? `${stats.tg_contacted_rate}% contacted` : `${Math.round((stats.tg_coverage / 100) * stats.total)} leads`,
              color: stats.tg_coverage >= 30 ? "text-emerald-500" : stats.tg_coverage >= 10 ? "text-amber-500" : "text-muted-foreground",
            },
            {
              label: "Twitter/X Coverage",
              value: stats.twitter_coverage,
              sub: `${Math.round((stats.twitter_coverage / 100) * stats.total)} leads`,
              color: stats.twitter_coverage >= 30 ? "text-emerald-500" : stats.twitter_coverage >= 10 ? "text-amber-500" : "text-muted-foreground",
            },
          ].map((card) => (
            <div key={card.label} className="rounded-xl border bg-card px-4 py-3">
              <p className="text-xs text-muted-foreground">{card.label}</p>
              <p className={`text-2xl font-semibold tabular-nums mt-1 ${card.color}`}>{card.value}%</p>
              <p className="text-xs text-muted-foreground mt-0.5">{card.sub}</p>
            </div>
          ))}
        </div>
      )}

      {/* CSV Upload */}
      <div
        {...getRootProps()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 transition-colors ${
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-muted-foreground/50"
        }`}
      >
        <input {...getInputProps()} />
        {importCSV.isPending ? (
          <p className="text-sm text-muted-foreground">Uploading...</p>
        ) : (
          <div className="text-center">
            <p className="text-sm font-medium">
              Drop a CSV to add leads, or click to browse
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Auto-detects LinkedIn URLs and maps columns
            </p>
          </div>
        )}
      </div>

      {/* Campaigns using this list */}
      <Card>
        <CardHeader>
          <CardTitle>Assigned Campaigns</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {list.campaigns.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Not assigned to any campaigns
            </p>
          ) : (
            <div className="space-y-2">
              {list.campaigns.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between rounded-md bg-muted px-3 py-2"
                >
                  <span className="text-sm">{c.name}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      unassign.mutate(
                        { listId: id, campaignId: c.id },
                        {
                          onSuccess: (data) =>
                            toast.success(
                              `Unassigned — ${data.leads_removed} leads removed`
                            ),
                          onError: (err) => toast.error(err.message),
                        }
                      )
                    }
                  >
                    Unassign
                  </Button>
                </div>
              ))}
            </div>
          )}

          {availableCampaigns.length > 0 && (
            <div className="flex gap-2 pt-2">
              <select
                className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={selectedCampaign}
                onChange={(e) => setSelectedCampaign(e.target.value)}
              >
                <option value="">Select campaign...</option>
                {availableCampaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <Button
                disabled={!selectedCampaign || assign.isPending}
                onClick={() => {
                  assign.mutate(
                    { listId: id, campaignId: selectedCampaign },
                    {
                      onSuccess: (data) => {
                        toast.success(`Assigned — ${data.leads_added} leads added`);
                        setSelectedCampaign("");
                      },
                      onError: (err) => toast.error(err.message),
                    }
                  );
                }}
              >
                Assign
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Leads table */}
      <Card>
        <CardHeader>
          <CardTitle>Leads ({leadsData?.total ?? 0})</CardTitle>
        </CardHeader>
        <CardContent>
          {!leadsData || leadsData.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No leads in this list</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 font-medium">Name</th>
                      <th className="pb-2 font-medium">Company</th>
                      <th className="pb-2 font-medium">Title</th>
                      <th className="pb-2 font-medium">LinkedIn</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leadsData.items.map((lead) => (
                      <tr key={lead.id} className="border-b last:border-0">
                        <td className="py-2">
                          <div className="flex items-center gap-0.5">
                            {[lead.first_name, lead.last_name].filter(Boolean).join(" ") || "—"}
                            {lead.email && <EmailCopyButton email={lead.email} />}
                          </div>
                        </td>
                        <td className="py-2 max-w-[160px]">
                          <span className="block truncate" title={lead.company ?? undefined}>{lead.company ?? "—"}</span>
                        </td>
                        <td className="py-2 max-w-[180px]">
                          <span className="block truncate" title={lead.title ?? undefined}>{lead.title ?? "—"}</span>
                        </td>
                        <td className="py-2">
                          <a
                            href={lead.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-500 hover:underline truncate block max-w-[200px] text-xs"
                          >
                            {lead.linkedin_url.replace("https://www.linkedin.com/in/", "")}
                          </a>
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
