"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  useCampaign,
  useCampaignStats,
  useAccount,
  useUpdateCampaign,
  useActivateCampaign,
  usePauseCampaign,
  useResetLeads,
  useLeadLists,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
  useArchiveCampaign,
} from "@/hooks/use-queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { DailyChart } from "@/components/stats/daily-chart";
import { LeadsTable } from "@/components/leads/leads-table";
import { CSVUpload } from "@/components/leads/csv-upload";
import { ActivityTimeline } from "@/components/activity-timeline";
import { Input } from "@/components/ui/input";
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
  const { data: account } = useAccount(campaign?.account_id ?? "", {
    enabled: !!campaign?.account_id,
  });
  const { data: campaignStats } = useCampaignStats(id);
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();
  const resetLeads = useResetLeads();
  const updateCampaign = useUpdateCampaign(id);
  const archiveCampaign = useArchiveCampaign();
  const { data: allLists } = useLeadLists();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const [selectedList, setSelectedList] = useState("");
  const [editName, setEditName] = useState("");
  const [editConnMsg, setEditConnMsg] = useState("");
  const [editFilterNoPhoto, setEditFilterNoPhoto] = useState(false);
  const [editMinConnections, setEditMinConnections] = useState("");
  const [editFollowupEnabled, setEditFollowupEnabled] = useState(false);
  const [editFollowupDelayHours, setEditFollowupDelayHours] = useState("");
  const [editFollowupMsg1, setEditFollowupMsg1] = useState("");
  const [editFollowupMsg2, setEditFollowupMsg2] = useState("");
  const [editFollowupMsg3, setEditFollowupMsg3] = useState("");
  const [editWeekendEnabled, setEditWeekendEnabled] = useState(false);
  const [settingsInit, setSettingsInit] = useState(false);

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

  if (!settingsInit) {
    setEditName(campaign.name);
    setEditConnMsg(campaign.connection_message_template ?? "");
    setEditFilterNoPhoto(campaign.filter_no_photo);
    setEditMinConnections(campaign.filter_min_connections != null ? String(campaign.filter_min_connections) : "");
    setEditFollowupEnabled(campaign.followup_enabled);
    setEditFollowupDelayHours(String(campaign.followup_delay_hours));
    setEditFollowupMsg1(campaign.followup_message_1 ?? "");
    setEditFollowupMsg2(campaign.followup_message_2 ?? "");
    setEditFollowupMsg3(campaign.followup_message_3 ?? "");
    setEditWeekendEnabled(campaign.weekend_enabled);
    setSettingsInit(true);
  }

  const counts = campaign.status_counts ?? {};
  const totalLeads = Object.values(counts).reduce((a, b) => a + b, 0);
  const pending = counts["pending"] ?? 0;
  const sent = counts["connection_requested"] ?? 0;
  const connected = counts["connected"] ?? 0;
  const errors = counts["error"] ?? 0;

  const assignedListIds = new Set((campaign.assigned_lists ?? []).map((l) => l.id));
  const availableLists = (allLists ?? []).filter((ll) => !assignedListIds.has(ll.id));

  function handleSaveSettings() {
    const data: Record<string, unknown> = {};
    if (editName !== campaign!.name) data.name = editName;
    if (editConnMsg !== (campaign!.connection_message_template ?? ""))
      data.connection_message_template = editConnMsg || null;
    if (editFilterNoPhoto !== campaign!.filter_no_photo) data.filter_no_photo = editFilterNoPhoto;
    const minConn = editMinConnections ? Number(editMinConnections) : null;
    if (minConn !== campaign!.filter_min_connections) data.filter_min_connections = minConn;
    if (editFollowupEnabled !== campaign!.followup_enabled) data.followup_enabled = editFollowupEnabled;
    const delayHours = editFollowupDelayHours ? Number(editFollowupDelayHours) : 0;
    if (delayHours !== campaign!.followup_delay_hours) data.followup_delay_hours = delayHours;
    if (editFollowupMsg1 !== (campaign!.followup_message_1 ?? "")) data.followup_message_1 = editFollowupMsg1 || null;
    if (editFollowupMsg2 !== (campaign!.followup_message_2 ?? "")) data.followup_message_2 = editFollowupMsg2 || null;
    if (editFollowupMsg3 !== (campaign!.followup_message_3 ?? "")) data.followup_message_3 = editFollowupMsg3 || null;
    if (editWeekendEnabled !== campaign!.weekend_enabled) data.weekend_enabled = editWeekendEnabled;
    if (Object.keys(data).length === 0) {
      toast.info("No changes to save");
      return;
    }
    updateCampaign.mutate(data, {
      onSuccess: () => toast.success("Campaign settings saved"),
      onError: (err) => toast.error(err.message),
    });
  }

  return (
    <div>
      {/* Sticky header */}
      <div className="sticky top-0 z-20 bg-background border-b border-border -mx-6 px-6 py-3 mb-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-lg font-semibold truncate">{campaign.name}</h1>
            <StatusBadge status={campaign.status} />
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {(campaign.status === "draft" || campaign.status === "paused") && (
              <Button
                size="sm"
                onClick={() => activate.mutate(id, { onSuccess: () => toast.success("Campaign activated") })}
                disabled={activate.isPending}
              >
                Activate
              </Button>
            )}
            {campaign.status === "active" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => pause.mutate(id, { onSuccess: () => toast.success("Campaign paused") })}
                disabled={pause.isPending}
              >
                Pause
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                resetLeads.mutate(id, {
                  onSuccess: (data) => toast.success(`Reset ${data.reset_count} leads`),
                })
              }
              disabled={resetLeads.isPending}
            >
              Reset Errors
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveSettings}
              disabled={updateCampaign.isPending}
            >
              Save Settings
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (confirm("Archive this campaign? It will be paused and hidden from the main view.")) {
                  archiveCampaign.mutate(id, {
                    onSuccess: () => {
                      toast.success("Campaign archived");
                      router.push("/campaigns");
                    },
                    onError: (err) => toast.error(err.message),
                  });
                }
              }}
              disabled={archiveCampaign.isPending}
            >
              Archive
            </Button>
          </div>
        </div>
      </div>

      <div className="space-y-6">
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

        {account?.paused_until && new Date(account.paused_until) > new Date() && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3">
            <p className="text-sm text-amber-400">
              Account <span className="font-medium">{account.name}</span> is paused due to weekly limit — resumes{" "}
              {new Date(account.paused_until).toLocaleString(undefined, {
                weekday: "short",
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </p>
          </div>
        )}

        <Tabs defaultValue="stats">
          <TabsList>
            <TabsTrigger value="stats">Stats</TabsTrigger>
            <TabsTrigger value="leads">Leads</TabsTrigger>
            <TabsTrigger value="lists">Lists</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
          </TabsList>

          <TabsContent value="stats" className="mt-4 space-y-4">
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <StatCard label="Total Sent" value={campaignStats?.summary.total_sent ?? 0} />
              <StatCard
                label="Accepted"
                value={campaignStats?.summary.total_accepted ?? 0}
                sub={
                  campaignStats?.summary.acceptance_rate != null
                    ? `${campaignStats.summary.acceptance_rate}% rate`
                    : undefined
                }
              />
              <StatCard
                label="Avg Time to Accept"
                value={
                  campaignStats?.summary.avg_time_to_accept_hours != null
                    ? `${campaignStats.summary.avg_time_to_accept_hours}h`
                    : "—"
                }
              />
              <StatCard label="Errors" value={errors} />
            </div>
            <Card>
              <CardHeader>
                <CardTitle>Daily Activity (30 days)</CardTitle>
              </CardHeader>
              <CardContent>
                <DailyChart
                  data={(campaignStats?.daily ?? []).map((d) => ({
                    date: d.date,
                    connection_requests_sent: d.sent,
                    connections_accepted: d.accepted,
                    followup_messages_sent: 0,
                    errors: d.errors,
                  }))}
                />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="leads" className="space-y-4 mt-4">
            <CSVUpload campaignId={id} />
            <LeadsTable campaignId={id} />
          </TabsContent>

          <TabsContent value="lists" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>Assigned Lists</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {(campaign.assigned_lists ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No lists assigned to this campaign.</p>
                ) : (
                  <div className="space-y-2">
                    {(campaign.assigned_lists ?? []).map((ll) => (
                      <div
                        key={ll.id}
                        className="flex items-center justify-between rounded-md bg-muted px-3 py-2"
                      >
                        <div>
                          <span className="text-sm font-medium">{ll.name}</span>
                          <span className="text-xs text-muted-foreground ml-2">{ll.total_leads} leads</span>
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={unassign.isPending}
                          onClick={() =>
                            unassign.mutate(
                              { listId: ll.id, campaignId: id },
                              {
                                onSuccess: (data) =>
                                  toast.success(`Unassigned — ${data.leads_removed} leads removed`),
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

                {availableLists.length > 0 && (
                  <div className="flex gap-2 pt-2">
                    <select
                      className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                      value={selectedList}
                      onChange={(e) => setSelectedList(e.target.value)}
                    >
                      <option value="">Select a list to assign...</option>
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
                              toast.success(`Assigned — ${data.leads_added} leads added`);
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

                {availableLists.length === 0 && (campaign.assigned_lists ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    No lists available.{" "}
                    <Link href="/lead-lists" className="text-blue-500 hover:underline">
                      Create one
                    </Link>
                  </p>
                )}

                <p className="text-xs text-muted-foreground pt-1">
                  Assigning a list copies its leads into this campaign. Unassigning marks those leads as removed.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="settings" className="mt-4 space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Campaign Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 max-w-2xl">
                <div>
                  <label className="text-xs text-muted-foreground">Campaign Name</label>
                  <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
                </div>
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Account</p>
                  <p className="text-sm">{campaign.account_name ?? campaign.account_id}</p>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Connection Message Template</label>
                  <textarea
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[100px] resize-y"
                    value={editConnMsg}
                    onChange={(e) => setEditConnMsg(e.target.value)}
                    placeholder="Hi {{first_name}}, I'd like to connect..."
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Use {"{{first_name}}"}, {"{{last_name}}"}, {"{{company}}"}, {"{{title}}"} as variables
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      id="filterNoPhoto"
                      checked={editFilterNoPhoto}
                      onChange={(e) => setEditFilterNoPhoto(e.target.checked)}
                    />
                    <label htmlFor="filterNoPhoto" className="text-sm">Skip profiles without photo</label>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Min Connections</label>
                    <Input
                      type="number"
                      value={editMinConnections}
                      onChange={(e) => setEditMinConnections(e.target.value)}
                      placeholder="No minimum"
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="weekendEnabled"
                    checked={editWeekendEnabled}
                    onChange={(e) => setEditWeekendEnabled(e.target.checked)}
                  />
                  <label htmlFor="weekendEnabled" className="text-sm">Run campaign on weekends</label>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Follow-up Messages</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 max-w-2xl">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="followupEnabled"
                    checked={editFollowupEnabled}
                    onChange={(e) => setEditFollowupEnabled(e.target.checked)}
                  />
                  <label htmlFor="followupEnabled" className="text-sm">
                    Send follow-up messages when connection is accepted
                  </label>
                </div>
                <p className="text-xs text-muted-foreground">
                  When enabled, 1-3 messages are sent after a connection is accepted.
                  Use {"{{first_name}}"}, {"{{last_name}}"}, {"{{company}}"}, {"{{title}}"} as variables.
                </p>
                <div>
                  <label className="text-xs text-muted-foreground">Delay after acceptance (hours)</label>
                  <Input
                    type="number"
                    min="0"
                    value={editFollowupDelayHours}
                    onChange={(e) => setEditFollowupDelayHours(e.target.value)}
                    placeholder="0"
                    disabled={!editFollowupEnabled}
                    className="mt-1 max-w-[200px]"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Message 1</label>
                  <textarea
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[80px] resize-y disabled:opacity-50"
                    value={editFollowupMsg1}
                    onChange={(e) => setEditFollowupMsg1(e.target.value)}
                    placeholder="Hi {{first_name}}, thanks for connecting!"
                    disabled={!editFollowupEnabled}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Message 2 (optional)</label>
                  <textarea
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[80px] resize-y disabled:opacity-50"
                    value={editFollowupMsg2}
                    onChange={(e) => setEditFollowupMsg2(e.target.value)}
                    placeholder="Second message..."
                    disabled={!editFollowupEnabled || !editFollowupMsg1}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Message 3 (optional)</label>
                  <textarea
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm min-h-[80px] resize-y disabled:opacity-50"
                    value={editFollowupMsg3}
                    onChange={(e) => setEditFollowupMsg3(e.target.value)}
                    placeholder="Third message..."
                    disabled={!editFollowupEnabled || !editFollowupMsg2}
                  />
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ActivityTimeline logs={campaignActivity} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
