"use client";

import { useState } from "react";
import Link from "next/link";
import { useCampaigns, useActivateCampaign, usePauseCampaign } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

export default function CampaignsPage() {
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const { data: campaigns, isLoading } = useCampaigns({ status: statusFilter });
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();

  function getProgress(counts: Record<string, number> | null | undefined) {
    if (!counts) return { total: 0, sent: 0, accepted: 0, other: 0, sentPct: 0, acceptedPct: 0, otherPct: 0 };
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return { total: 0, sent: 0, accepted: 0, other: 0, sentPct: 0, acceptedPct: 0, otherPct: 0 };
    const accepted = (counts["connected"] ?? 0) + (counts["followup_scheduled"] ?? 0) + (counts["followup_sent"] ?? 0) + (counts["completed"] ?? 0);
    const sent = counts["connection_requested"] ?? 0;
    const pending = (counts["pending"] ?? 0) + (counts["scheduled"] ?? 0);
    const other = total - pending - sent - accepted;
    return {
      total,
      sent,
      accepted,
      other,
      sentPct: Math.round((sent / total) * 100),
      acceptedPct: Math.round((accepted / total) * 100),
      otherPct: Math.round((other / total) * 100),
    };
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Campaigns" description="Manage your LinkedIn outreach campaigns">
        <Link href="/campaigns/new">
          <Button>Create Campaign</Button>
        </Link>
      </PageHeader>

      <Tabs
        defaultValue="all"
        onValueChange={(v) => setStatusFilter(v === "all" ? undefined : v)}
      >
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="active">Active</TabsTrigger>
          <TabsTrigger value="paused">Paused</TabsTrigger>
          <TabsTrigger value="draft">Draft</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : campaigns?.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">No campaigns yet</p>
            <Link href="/campaigns/new">
              <Button variant="outline" className="mt-4">
                Create your first campaign
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {campaigns?.map((c) => {
            const progress = getProgress(c.status_counts);
            return (
              <Link key={c.id} href={`/campaigns/${c.id}`}>
                <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
                  <CardContent className="p-5 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-medium truncate">{c.name}</h3>
                      <StatusBadge status={c.status} />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {c.account_name ?? "—"}
                    </p>
                    {c.account_status === "cookie_expired" ? (
                      <p className="text-xs text-red-400">
                        Cookie expired — update in{" "}
                        <Link
                          href={`/accounts/${c.account_id}`}
                          className="underline hover:text-red-300"
                          onClick={(e) => e.stopPropagation()}
                        >
                          account settings
                        </Link>
                      </p>
                    ) : c.account_paused_until && new Date(c.account_paused_until) > new Date() ? (
                      <p className="text-xs text-amber-400">
                        Paused — resumes{" "}
                        {new Date(c.account_paused_until.endsWith("Z") ? c.account_paused_until : c.account_paused_until + "Z").toLocaleString(undefined, {
                          timeZone: c.account_timezone ?? undefined,
                          weekday: "short",
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </p>
                    ) : null}
                    <div className="space-y-1">
                      <div className="flex justify-between text-xs text-muted-foreground">
                        <span>{progress.accepted + progress.sent + progress.other} / {progress.total} processed</span>
                        <span>{progress.acceptedPct + progress.sentPct + progress.otherPct}%</span>
                      </div>
                      <div className="flex h-1.5 w-full rounded-full bg-muted overflow-hidden">
                        {progress.acceptedPct > 0 && (
                          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${progress.acceptedPct}%` }} />
                        )}
                        {progress.sentPct > 0 && (
                          <div className="h-full bg-blue-500 transition-all" style={{ width: `${progress.sentPct}%` }} />
                        )}
                        {progress.otherPct > 0 && (
                          <div className="h-full bg-zinc-500 transition-all" style={{ width: `${progress.otherPct}%` }} />
                        )}
                      </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                      {c.status === "draft" || c.status === "paused" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={(e) => {
                            e.preventDefault();
                            activate.mutate(c.id, {
                              onSuccess: () => toast.success("Campaign activated"),
                            });
                          }}
                        >
                          Activate
                        </Button>
                      ) : c.status === "active" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={(e) => {
                            e.preventDefault();
                            pause.mutate(c.id, {
                              onSuccess: () => toast.success("Campaign paused"),
                            });
                          }}
                        >
                          Pause
                        </Button>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
