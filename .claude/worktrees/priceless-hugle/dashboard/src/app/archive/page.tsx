"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  useAccounts,
  useCampaigns,
  useLeadLists,
  useUnarchiveAccount,
  useUnarchiveCampaign,
  useUnarchiveLeadList,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

type FilterType = "all" | "campaigns" | "accounts" | "lists";

export default function ArchivePage() {
  const [filter, setFilter] = useState<FilterType>("all");
  const router = useRouter();

  const { data: accounts, isLoading: loadingAccounts } = useAccounts({ include_archived: true });
  const { data: campaigns, isLoading: loadingCampaigns } = useCampaigns({ include_archived: true });
  const { data: lists, isLoading: loadingLists } = useLeadLists({ include_archived: true });

  const unarchiveAccount = useUnarchiveAccount();
  const unarchiveCampaign = useUnarchiveCampaign();
  const unarchiveList = useUnarchiveLeadList();

  const archivedAccounts = accounts?.filter((a) => a.archived) ?? [];
  const archivedCampaigns = campaigns?.filter((c) => c.archived) ?? [];
  const archivedLists = lists?.filter((l) => l.archived) ?? [];

  const isLoading = loadingAccounts || loadingCampaigns || loadingLists;

  const totalArchived = archivedAccounts.length + archivedCampaigns.length + archivedLists.length;

  const filterButtons: { label: string; value: FilterType; count: number }[] = [
    { label: "All", value: "all", count: totalArchived },
    { label: "Campaigns", value: "campaigns", count: archivedCampaigns.length },
    { label: "Accounts", value: "accounts", count: archivedAccounts.length },
    { label: "Lists", value: "lists", count: archivedLists.length },
  ];

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Archive"
        description="Archived items are hidden from main views and paused. Restore to resume."
      />

      {/* Filter tabs */}
      <div className="flex gap-2">
        {filterButtons.map((btn) => (
          <button
            key={btn.value}
            onClick={() => setFilter(btn.value)}
            className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
              filter === btn.value
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            }`}
          >
            {btn.label}
            {btn.count > 0 && (
              <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-xs">
                {btn.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {totalArchived === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <p className="text-muted-foreground">Nothing archived yet</p>
            <p className="text-sm text-muted-foreground mt-1">
              Use the Archive button on campaigns, accounts, or lists to move them here.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {/* Campaigns */}
        {(filter === "all" || filter === "campaigns") &&
          archivedCampaigns.map((c) => (
            <Card key={c.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                    Campaign
                  </span>
                  <div className="min-w-0">
                    <p className="font-medium truncate">{c.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {c.account_name ?? c.account_id} · {c.total_leads} leads
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => router.push(`/campaigns/${c.id}`)}
                  >
                    View
                  </Button>
                  <Button
                    size="sm"
                    onClick={() =>
                      unarchiveCampaign.mutate(c.id, {
                        onSuccess: () => toast.success(`${c.name} restored`),
                        onError: (err) => toast.error(err.message),
                      })
                    }
                    disabled={unarchiveCampaign.isPending}
                  >
                    Restore
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}

        {/* Accounts */}
        {(filter === "all" || filter === "accounts") &&
          archivedAccounts.map((a) => (
            <Card key={a.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                    Account
                  </span>
                  <div className="min-w-0">
                    <p className="font-medium truncate">{a.name}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <StatusBadge status={a.status} />
                      <span className="text-xs text-muted-foreground">{a.timezone ?? "—"}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => router.push(`/accounts/${a.id}`)}
                  >
                    View
                  </Button>
                  <Button
                    size="sm"
                    onClick={() =>
                      unarchiveAccount.mutate(a.id, {
                        onSuccess: () => toast.success(`${a.name} restored`),
                        onError: (err) => toast.error(err.message),
                      })
                    }
                    disabled={unarchiveAccount.isPending}
                  >
                    Restore
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}

        {/* Lists */}
        {(filter === "all" || filter === "lists") &&
          archivedLists.map((l) => (
            <Card key={l.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                    List
                  </span>
                  <div className="min-w-0">
                    <p className="font-medium truncate">{l.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {l.total_leads} leads · {l.campaign_count} campaigns
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => router.push(`/lead-lists/${l.id}`)}
                  >
                    View
                  </Button>
                  <Button
                    size="sm"
                    onClick={() =>
                      unarchiveList.mutate(l.id, {
                        onSuccess: () => toast.success(`${l.name} restored`),
                        onError: (err) => toast.error(err.message),
                      })
                    }
                    disabled={unarchiveList.isPending}
                  >
                    Restore
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}

        {/* Empty state for current filter */}
        {totalArchived > 0 &&
          ((filter === "campaigns" && archivedCampaigns.length === 0) ||
            (filter === "accounts" && archivedAccounts.length === 0) ||
            (filter === "lists" && archivedLists.length === 0)) && (
            <Card>
              <CardContent className="py-10 text-center">
                <p className="text-sm text-muted-foreground">No archived {filter} yet</p>
              </CardContent>
            </Card>
          )}
      </div>
    </div>
  );
}
