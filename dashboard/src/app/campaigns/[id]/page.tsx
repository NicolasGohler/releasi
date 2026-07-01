"use client";

import { use, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { CampaignActivityChart } from "@/components/stats/campaign-chart";
import {
  useCampaign,
  useCampaignStats,
  useAccount,
  useAccounts,
  useUpdateCampaign,
  useActivateCampaign,
  usePauseCampaign,
  useResetLeads,
  useLeadLists,
  useAssignListToCampaign,
  useUnassignListFromCampaign,
  useReorderCampaignLists,
  useArchiveCampaign,
  useLeads,
  useCloneCampaign,
} from "@/hooks/use-queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { LeadsTable } from "@/components/leads/leads-table";
import { CSVUpload } from "@/components/leads/csv-upload";
import { ActivityTimeline } from "@/components/activity-timeline";
import { MessageTemplateEditor } from "@/components/message-template-editor";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { fetchAccountActivity, replanAccount } from "@/lib/api";
import { ResumeCampaignDialog, shouldOfferCatchup } from "@/components/resume-campaign-dialog";

type AssignedList = {
  id: string;
  name: string;
  total_leads: number;
  status_counts?: Record<string, number> | null;
};

function SortableListRow({
  ll,
  idx,
  total,
  listsCount,
  onUnassign,
  isUnassigning,
}: {
  ll: AssignedList;
  idx: number;
  total: number;
  listsCount: number;
  onUnassign: () => void;
  isUnassigning: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: ll.id });

  const counts = ll.status_counts ?? {};
  const accepted = (counts["connected"] ?? 0) + (counts["followup_scheduled"] ?? 0) + (counts["followup_sent"] ?? 0) + (counts["completed"] ?? 0);
  const sent = counts["connection_requested"] ?? 0;
  const other = total - (counts["pending"] ?? 0) - (counts["scheduled"] ?? 0) - accepted - sent;
  const acceptedPct = total > 0 ? Math.round((accepted / total) * 100) : 0;
  const sentPct = total > 0 ? Math.round((sent / total) * 100) : 0;
  const otherPct = total > 0 ? Math.round((other / total) * 100) : 0;
  const processedPct = acceptedPct + sentPct + otherPct;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`rounded-md bg-muted px-3 py-2 space-y-2 ${isDragging ? "opacity-50 shadow-lg" : ""}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {listsCount > 1 && (
            <button
              type="button"
              className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground touch-none"
              {...attributes}
              {...listeners}
            >
              <GripVertical className="h-4 w-4" />
            </button>
          )}
          <div>
            <Link
              href={`/lead-lists/${ll.id}`}
              className="text-sm font-medium hover:underline"
            >
              {ll.name}
            </Link>
            {idx === 0 && listsCount > 1 && (
              <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-600 ml-2">
                Priority
              </span>
            )}
            <span className="text-xs text-muted-foreground ml-2">{ll.total_leads} leads</span>
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={isUnassigning}
          onClick={onUnassign}
        >
          Unassign
        </Button>
      </div>
      {total > 0 && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{accepted + sent + other} / {total} processed</span>
            <span>{processedPct}%</span>
          </div>
          <div className="flex h-1.5 w-full rounded-full bg-background overflow-hidden">
            {acceptedPct > 0 && (
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${acceptedPct}%` }} />
            )}
            {sentPct > 0 && (
              <div className="h-full bg-blue-500 transition-all" style={{ width: `${sentPct}%` }} />
            )}
            {otherPct > 0 && (
              <div className="h-full bg-zinc-500 transition-all" style={{ width: `${otherPct}%` }} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: campaign, isLoading } = useCampaign(id);
  const { data: campaignStats } = useCampaignStats(id); // summary.total_sent is always all-time
  const { data: account } = useAccount(campaign?.account_id ?? "", {
    enabled: !!campaign?.account_id,
  });
  const activate = useActivateCampaign();
  const pause = usePauseCampaign();
  const resetLeads = useResetLeads();
  const [resumeDialogOpen, setResumeDialogOpen] = useState(false);
  const updateCampaign = useUpdateCampaign(id);
  const archiveCampaign = useArchiveCampaign();
  const cloneCampaign = useCloneCampaign();
  const { data: allLists } = useLeadLists();
  const { data: allAccounts } = useAccounts();
  const assign = useAssignListToCampaign();
  const unassign = useUnassignListFromCampaign();
  const reorderLists = useReorderCampaignLists();
  const { data: leadSample } = useLeads(id, { per_page: 200 });
  const dndSensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );
  const handleListDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const lists = campaign?.assigned_lists ?? [];
    const oldIndex = lists.findIndex((l) => l.id === active.id);
    const newIndex = lists.findIndex((l) => l.id === over.id);
    const reordered = arrayMove(lists, oldIndex, newIndex);
    reorderLists.mutate(
      { campaignId: id, orderedListIds: reordered.map((l) => l.id) },
      { onError: (err) => toast.error(err.message) }
    );
  };

  // Clone dialog state
  const [cloneDialogOpen, setCloneDialogOpen] = useState(false);
  const [cloneName, setCloneName] = useState("");
  const [cloneAccountId, setCloneAccountId] = useState("");

  // Compute per-variable fill rates from the lead sample (0–1).
  const fillRates = useMemo(() => {
    const leads = leadSample?.items;
    if (!leads || leads.length === 0) return undefined;
    const total = leads.length;
    const FIELD_MAP: Record<string, "first_name" | "last_name" | "company" | "title"> = {
      first_name: "first_name",
      firstname:  "first_name",
      last_name:  "last_name",
      lastname:   "last_name",
      company:    "company",
      title:      "title",
    };
    const rates: Record<string, number> = {};
    for (const [token, field] of Object.entries(FIELD_MAP)) {
      const filled = leads.filter(l => l[field] != null && l[field] !== "").length;
      rates[token] = filled / total;
    }
    // extra_data keys
    const extraKeys = new Set<string>();
    for (const lead of leads) {
      if (lead.extra_data) {
        for (const k of Object.keys(lead.extra_data)) extraKeys.add(k.toLowerCase());
      }
    }
    for (const key of extraKeys) {
      const filled = leads.filter(l =>
        l.extra_data && key in l.extra_data &&
        l.extra_data[key] != null && l.extra_data[key] !== ""
      ).length;
      rates[key] = filled / total;
    }
    return rates;
  }, [leadSample]);

  const [selectedList, setSelectedList] = useState("");
  const [editName, setEditName] = useState("");
  const [editConnMsg, setEditConnMsg] = useState("");
  const [editFilterNoPhoto, setEditFilterNoPhoto] = useState(false);
  const [editMinConnections, setEditMinConnections] = useState("");
  const [editExcludeOpenToWork, setEditExcludeOpenToWork] = useState(false);
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

  // Include campaign-scoped entries plus account-scoped system entries
  // (e.g. ACCEPTANCE_CHECK_SUMMARY has no campaign_id but is relevant here).
  const campaignActivity =
    activity?.filter(
      (a) =>
        a.campaign_id === id ||
        a.action_type.toUpperCase() === "ACCEPTANCE_CHECK_SUMMARY",
    ) ?? [];

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
    setEditExcludeOpenToWork(campaign.filter_exclude_open_to_work);
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
  // total_sent from action_log (authoritative): counts all successful dispatches
  // regardless of current lead status (accepted, withdrawn, still pending, etc.)
  const sent = campaignStats?.summary.total_sent ?? (counts["connection_requested"] ?? 0);
  // "connected" includes all post-acceptance statuses: CONNECTED (awaiting followup),
  // FOLLOWUP_SCHEDULED, FOLLOWUP_SENT, COMPLETED. Leads move through these states
  // sequentially, so counting only CONNECTED would show 0 once followup is dispatched.
  const connected =
    (counts["connected"] ?? 0) +
    (counts["followup_scheduled"] ?? 0) +
    (counts["followup_sent"] ?? 0) +
    (counts["completed"] ?? 0);
  const errors = counts["error"] ?? 0;
  const acceptRate = campaignStats?.summary.acceptance_rate ?? null;

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
    if (editExcludeOpenToWork !== campaign!.filter_exclude_open_to_work) data.filter_exclude_open_to_work = editExcludeOpenToWork;
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

    // Warn if any saved template references a variable that is empty for every lead.
    if (fillRates) {
      const templatesToCheck = [editConnMsg, editFollowupMsg1, editFollowupMsg2, editFollowupMsg3].filter(Boolean);
      const zeroVars = new Set<string>();
      for (const tmpl of templatesToCheck) {
        for (const m of tmpl.matchAll(/\{\{(\w+)\}\}/g)) {
          if (fillRates[m[1].toLowerCase()] === 0) zeroVars.add(m[1].toLowerCase());
        }
      }
      if (zeroVars.size > 0) {
        toast.warning(
          `${[...zeroVars].map(v => `{{${v}}}`).join(", ")} ${zeroVars.size === 1 ? "has" : "have"} no data for any lead — will always render as empty.`
        );
      }
    }

    updateCampaign.mutate(data, {
      onSuccess: () => {
        toast.success("Campaign settings saved");
        if (campaign?.account_id) {
          replanAccount(campaign.account_id).then(() =>
            toast.success("Schedule regenerated (1 lead queued immediately)")
          ).catch(() => {});
        }
      },
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
            {account && (
              <Link
                href={`/accounts/${campaign.account_id}`}
                className="hidden sm:flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                {account.name}
              </Link>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {(campaign.status === "draft" || campaign.status === "paused") && (
              <Button
                size="sm"
                onClick={() => {
                  if (campaign.status === "paused" && shouldOfferCatchup(campaign)) {
                    setResumeDialogOpen(true);
                  } else {
                    activate.mutate(id, { onSuccess: () => toast.success("Campaign activated") });
                  }
                }}
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
              variant="outline"
              onClick={() => {
                setCloneName(`${campaign.name} (copy)`);
                setCloneAccountId(campaign.account_id);
                setCloneDialogOpen(true);
              }}
            >
              Clone
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
        {/* Summary strip — always visible above tabs */}
        <div className={`grid gap-3 ${campaign.account_dispatch_mode === "continuous" ? "grid-cols-3 md:grid-cols-7" : "grid-cols-3 md:grid-cols-6"}`}>
          <StatCard label="Total Leads" value={totalLeads} />
          <StatCard label="Pending" value={pending} />
          <StatCard label="Sent" value={sent} />
          <StatCard label="Connected" value={connected} />
          <StatCard
            label="Accept Rate"
            value={acceptRate !== null ? `${acceptRate}%` : "—"}
          />
          <StatCard label="Errors" value={errors} />
          {campaign.account_dispatch_mode === "continuous" && (
            <StatCard
              label="Expected Today"
              value={campaign.estimated_remaining_today ?? "—"}
            />
          )}
        </div>

        {account?.paused_until && new Date(account.paused_until) > new Date() && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3">
            <p className="text-sm text-amber-400">
              Account <span className="font-medium">{account.name}</span> is paused due to weekly limit — resumes{" "}
              {new Date(account.paused_until.endsWith("Z") ? account.paused_until : account.paused_until + "Z").toLocaleString(undefined, {
                timeZone: account.timezone ?? undefined,
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
            <Card>
              <CardContent className="pt-6">
                <CampaignActivityChart campaignId={id} totalLeads={totalLeads} />
              </CardContent>
            </Card>

            {/* Conversion Funnel */}
            {totalLeads > 0 && (() => {
              const sc = campaign.status_counts ?? {};
              const removed = sc["removed"] ?? 0;
              const imported = totalLeads - removed;
              const requested = (sc["connection_requested"] ?? 0) + (sc["connected"] ?? 0) + (sc["followup_scheduled"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);
              const connectedCount = (sc["connected"] ?? 0) + (sc["followup_scheduled"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);
              const followedUp = (sc["followup_sent"] ?? 0) + (sc["completed"] ?? 0);

              const funnelStages = [
                { label: "Imported", count: imported, base: imported },
                { label: "Requested", count: requested, base: imported },
                { label: "Connected", count: connectedCount, base: requested },
                { label: "Followed Up", count: followedUp, base: connectedCount },
              ];

              return (
                <Card>
                  <CardHeader>
                    <CardTitle>Conversion Funnel</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      {funnelStages.map((stage, i) => {
                        const pct = stage.base > 0 ? Math.round((stage.count / stage.base) * 100) : 0;
                        const barWidth = stage.base > 0 ? (stage.count / stage.base) * 100 : 0;
                        return (
                          <div key={stage.label} className="grid grid-cols-[100px_1fr_60px_48px] items-center gap-3">
                            <span className="text-xs text-muted-foreground text-right truncate">{stage.label}</span>
                            <div className="h-5 w-full rounded bg-muted overflow-hidden">
                              <div
                                className={`h-full rounded transition-all ${i === 0 ? "bg-zinc-500" : i === 1 ? "bg-blue-500" : i === 2 ? "bg-emerald-500" : "bg-emerald-600"}`}
                                style={{ width: `${barWidth}%` }}
                              />
                            </div>
                            <span className="text-xs text-right tabular-nums">{stage.count.toLocaleString()}</span>
                            <span className={`text-xs text-right tabular-nums font-medium ${i === 0 ? "text-muted-foreground" : pct >= 40 ? "text-emerald-500" : pct >= 20 ? "text-amber-500" : "text-red-400"}`}>
                              {i === 0 ? "100%" : `${pct}%`}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-xs text-muted-foreground mt-3">Requested, Connected, and Followed Up percentages are relative to the previous stage.</p>
                  </CardContent>
                </Card>
              );
            })()}

            {(campaign.assigned_lists ?? []).length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>List Performance</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                          <th className="pb-2 font-medium">List</th>
                          <th className="pb-2 font-medium text-right">Leads</th>
                          <th className="pb-2 font-medium text-right">Sent</th>
                          <th className="pb-2 font-medium text-right">Connected</th>
                          <th className="pb-2 font-medium text-right">Accept Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(campaign.assigned_lists ?? []).map((ll) => {
                          const sc = ll.status_counts ?? {};
                          const listConnected = (sc["connected"] ?? 0) + (sc["completed"] ?? 0) + (sc["followup_sent"] ?? 0) + (sc["followup_scheduled"] ?? 0);
                          const listSent = sc["connection_requested"] ?? 0;
                          const listWithdrawn = sc["withdrawn"] ?? 0;
                          const listProcessed = listConnected + listSent + listWithdrawn;
                          const listRate = listProcessed > 0 ? Math.round((listConnected / listProcessed) * 100) : null;
                          return (
                            <tr key={ll.id} className="border-b border-border/50 last:border-0">
                              <td className="py-2 font-medium">{ll.name}</td>
                              <td className="py-2 text-right text-muted-foreground">{ll.total_leads}</td>
                              <td className="py-2 text-right text-muted-foreground">{listSent + listConnected + listWithdrawn}</td>
                              <td className="py-2 text-right text-muted-foreground">{listConnected}</td>
                              <td className="py-2 text-right">
                                {listRate !== null ? (
                                  <span className={
                                    listRate >= 30 ? "text-emerald-500" :
                                    listRate >= 15 ? "text-amber-500" :
                                    "text-red-500"
                                  }>
                                    {listRate}%
                                  </span>
                                ) : (
                                  <span className="text-muted-foreground">—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="leads" className="space-y-4 mt-4">
            <CSVUpload campaignId={id} />
            <LeadsTable
              campaignId={id}
              timezone={account?.timezone}
              assignedLists={campaign.assigned_lists}
              campaignName={campaign.name}
            />
          </TabsContent>

          <TabsContent value="lists" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>Assigned Lists</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {(campaign.assigned_lists ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">No lists assigned to this campaign.</p>
                )}
                {(campaign.assigned_lists ?? []).length > 0 && (
                  <div className="space-y-2">
                    {(campaign.assigned_lists ?? []).length > 1 && (
                      <p className="text-xs text-muted-foreground">
                        Leads are dispatched top-to-bottom — drag to reorder.
                      </p>
                    )}
                    <DndContext sensors={dndSensors} collisionDetection={closestCenter} onDragEnd={handleListDragEnd}>
                      <SortableContext items={(campaign.assigned_lists ?? []).map((l) => l.id)} strategy={verticalListSortingStrategy}>
                        {(campaign.assigned_lists ?? []).map((ll, idx) => {
                          const counts = ll.status_counts ?? {};
                          const total = Object.values(counts).reduce((a: number, b: number) => a + b, 0);
                          return (
                            <SortableListRow
                              key={ll.id}
                              ll={ll}
                              idx={idx}
                              total={total}
                              listsCount={(campaign.assigned_lists ?? []).length}
                              isUnassigning={unassign.isPending}
                              onUnassign={() =>
                                unassign.mutate(
                                  { listId: ll.id, campaignId: id },
                                  {
                                    onSuccess: (data) =>
                                      toast.success(`Unassigned — ${data.leads_removed} leads removed`),
                                    onError: (err) => toast.error(err.message),
                                  }
                                )
                              }
                            />
                          );
                        })}
                      </SortableContext>
                    </DndContext>
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
                  <label className="text-xs text-muted-foreground mb-1 block">
                    Connection Message Template
                    <span className="ml-2 text-muted-foreground/70">(optional — leave empty to send without a note)</span>
                  </label>
                  <MessageTemplateEditor
                    value={editConnMsg}
                    onChange={setEditConnMsg}
                    placeholder="Hi {{first_name}}, I came across your profile and would love to connect."
                    minHeight={110}
                    showCharLimit
                    fillRates={fillRates}
                  />
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
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      id="filterExcludeOpenToWork"
                      checked={editExcludeOpenToWork}
                      onChange={(e) => setEditExcludeOpenToWork(e.target.checked)}
                    />
                    <label htmlFor="filterExcludeOpenToWork" className="text-sm">Skip &ldquo;Open to Work&rdquo; profiles</label>
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
                  Up to three messages, sent in order. Delivery waits for the delay
                  below, then uses normal send-window rules. Variables are substituted
                  per lead — click a chip above any field to insert.
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
                  <label className="text-xs text-muted-foreground mb-1 block">Message 1</label>
                  <MessageTemplateEditor
                    value={editFollowupMsg1}
                    onChange={setEditFollowupMsg1}
                    placeholder="Hi {{first_name}}, thanks for connecting! I wanted to reach out because…"
                    disabled={!editFollowupEnabled}
                    minHeight={96}
                    fillRates={fillRates}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">
                    Message 2 <span className="text-muted-foreground/70">(optional)</span>
                  </label>
                  <MessageTemplateEditor
                    value={editFollowupMsg2}
                    onChange={setEditFollowupMsg2}
                    placeholder="Second message — sent after message 1 goes through."
                    disabled={!editFollowupEnabled || !editFollowupMsg1}
                    minHeight={96}
                    fillRates={fillRates}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">
                    Message 3 <span className="text-muted-foreground/70">(optional)</span>
                  </label>
                  <MessageTemplateEditor
                    value={editFollowupMsg3}
                    onChange={setEditFollowupMsg3}
                    placeholder="Third message — last in the sequence."
                    disabled={!editFollowupEnabled || !editFollowupMsg2}
                    minHeight={96}
                    fillRates={fillRates}
                  />
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ActivityTimeline logs={campaignActivity} timezone={account?.timezone} />
          </TabsContent>
        </Tabs>
      </div>
      <ResumeCampaignDialog
        campaign={campaign}
        open={resumeDialogOpen}
        onOpenChange={setResumeDialogOpen}
      />

      {/* Clone Campaign Dialog */}
      {cloneDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setCloneDialogOpen(false)}>
          <div className="bg-background border border-border rounded-xl shadow-xl w-full max-w-md mx-4 p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-base font-semibold">Clone Campaign</h2>
            <p className="text-sm text-muted-foreground">Copy all message templates, filters, and list assignments to a new campaign.</p>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">New Campaign Name</label>
                <input
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                  value={cloneName}
                  onChange={(e) => setCloneName(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Assign to Account</label>
                <select
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={cloneAccountId}
                  onChange={(e) => setCloneAccountId(e.target.value)}
                >
                  <option value="">Select an account...</option>
                  {(allAccounts ?? []).filter(a => !a.archived).map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button size="sm" variant="outline" onClick={() => setCloneDialogOpen(false)}>Cancel</Button>
              <Button
                size="sm"
                disabled={!cloneName.trim() || !cloneAccountId || cloneCampaign.isPending}
                onClick={() => {
                  cloneCampaign.mutate(
                    { id, name: cloneName.trim(), account_id: cloneAccountId },
                    {
                      onSuccess: (newCampaign) => {
                        toast.success("Campaign cloned");
                        setCloneDialogOpen(false);
                        router.push(`/campaigns/${newCampaign.id}`);
                      },
                      onError: (err) => toast.error(err.message),
                    }
                  );
                }}
              >
                {cloneCampaign.isPending ? "Cloning…" : "Clone"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
