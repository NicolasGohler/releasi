"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  useCampaign,
  useActivateCampaign,
  usePauseCampaign,
  useResetLeads,
  useLeadLists,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { LeadsTable } from "@/components/leads/leads-table";
import { CSVUpload } from "@/components/leads/csv-upload";
import { ActivityTimeline } from "@/components/activity-timeline";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { fetchAccountActivity } from "@/lib/api";

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: campaign, isLoading } = useCampaign(id);
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();
  const resetLeads = useResetLeads();
  const { data: allLists } = useLeadLists();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const [selectedList, setSelectedList] = useState("");

  // Fetch campaign-specific activity via account activity (filtered client-side)
  const { data: activity } = useQuery({
    queryKey: ["campaign-activity", campaign?.account_id],
    queryFn: () => fetchAccountActivity(campaign!.account_id),
    enabled: !!campaign?.account_id,
  });

  const campaignActivity = activity?.filter((a) => a.campaign_id === id) ?? [];

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      </div>
    );
  }

  if (!campaign) {
    return <p className="text-muted-foreground">Campaign not found</p>;
  }

  const counts = campaign.status_counts ?? {};
  const totalLeads = Object.values(counts).reduce((a, b) => a + b, 0);
  const pending = counts["pending"] ?? 0;
  const sent = counts["connection_requested"] ?? 0;
  const connected = counts["connected"] ?? 0;
  const errors = counts["error"] ?? 0;

  // Determine which lists are already assigned to this campaign
  const assignedListIds = new Set(
    allLists
      ?.filter((ll) => ll.campaign_count > 0)
      ?.map((ll) => ll.id) ?? []
  );
  // We don't have per-campaign assignment info from the list endpoint,
  // so we show all lists as assignable and let the API dedup
  const availableLists = allLists ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title={campaign.name}>
        <StatusBadge status={campaign.status} />
        {(campaign.status === "draft" || campaign.status === "paused") && (
          <Button
            onClick={() =>
              activate.mutate(id, {
                onSuccess: () => toast.success("Campaign activated"),
              })
            }
          >
            Activate
          </Button>
        )}
        {campaign.status === "active" && (
          <Button
            variant="outline"
            onClick={() =>
              pause.mutate(id, { onSuccess: () => toast.success("Campaign paused") })
            }
          >
            Pause
          </Button>
        )}
        <Button
          variant="outline"
          onClick={() =>
            resetLeads.mutate(id, {
              onSuccess: (data) => toast.success(`Reset ${data.reset_count} leads`),
            })
          }
        >
          Reset Errors
        </Button>
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Total Leads" value={totalLeads} />
        <StatCard label="Pending" value={pending} />
        <StatCard label="Sent" value={sent} />
        <StatCard
          label="Connected"
          value={connected}
          sub={totalLeads > 0 ? `${Math.round((connected / totalLeads) * 100)}% rate` : undefined}
        />
      </div>

      <Tabs defaultValue="leads">
        <TabsList>
          <TabsTrigger value="leads">Leads</TabsTrigger>
          <TabsTrigger value="lists">Lead Lists</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="leads" className="space-y-4 mt-4">
          <CSVUpload campaignId={id} />
          <LeadsTable campaignId={id} />
        </TabsContent>

        <TabsContent value="lists" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Assigned Lead Lists</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {availableLists.length > 0 && (
                <div className="flex gap-2">
                  <select
                    className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                    value={selectedList}
                    onChange={(e) => setSelectedList(e.target.value)}
                  >
                    <option value="">Select a lead list...</option>
                    {availableLists.map((ll) => (
                      <option key={ll.id} value={ll.id}>
                        {ll.name} ({ll.total_leads} leads)
                      </option>
                    ))}
                  </select>
                  <Button
                    disabled={!selectedList || assign.isPending}
                    onClick={() => {
                      assign.mutate(
                        { listId: selectedList, campaignId: id },
                        {
                          onSuccess: (data) => {
                            toast.success(
                              `Assigned — ${data.leads_added} leads added`
                            );
                            setSelectedList("");
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

              {availableLists.length === 0 && (
                <div className="text-center py-4">
                  <p className="text-sm text-muted-foreground">
                    No lead lists available.{" "}
                    <Link
                      href="/lead-lists"
                      className="text-blue-500 hover:underline"
                    >
                      Create one
                    </Link>
                  </p>
                </div>
              )}

              <p className="text-xs text-muted-foreground pt-2">
                Assigning a list copies its leads into this campaign. Unassigning marks those leads as removed.
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="settings" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Campaign Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <p className="text-xs text-muted-foreground">Account</p>
                <p className="text-sm">{campaign.account_name ?? campaign.account_id}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Connection Message</p>
                <pre className="text-sm bg-muted p-3 rounded-md whitespace-pre-wrap mt-1">
                  {campaign.connection_message_template || "(no template)"}
                </pre>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Follow-up Message</p>
                <pre className="text-sm bg-muted p-3 rounded-md whitespace-pre-wrap mt-1">
                  {campaign.followup_message_template || "(no template)"}
                </pre>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Follow-up Delay</p>
                  <p className="text-sm">{campaign.followup_delay_hours}h</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Filter: No Photo</p>
                  <p className="text-sm">{campaign.filter_no_photo ? "Yes" : "No"}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Min Connections</p>
                  <p className="text-sm">{campaign.filter_min_connections ?? "—"}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="activity" className="mt-4">
          <ActivityTimeline logs={campaignActivity} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
