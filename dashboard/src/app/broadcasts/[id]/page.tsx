"use client";

import { useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useBroadcast,
  useBroadcastLeads,
  useActivateBroadcast,
  usePauseBroadcast,
  useArchiveBroadcast,
  useDeleteBroadcast,
  useUpdateBroadcast,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MessageTemplateEditor } from "@/components/message-template-editor";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Play, Pause, Archive, Trash2, Pencil } from "lucide-react";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  sent: "Msg sent",
  sequence_complete: "Complete",
  skipped: "Skipped",
  manual_outreach: "Manual",
  error: "Error",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-zinc-500/20 text-zinc-400",
  sent: "bg-blue-500/20 text-blue-400",
  sequence_complete: "bg-emerald-500/20 text-emerald-400",
  skipped: "bg-amber-500/20 text-amber-400",
  manual_outreach: "bg-purple-500/20 text-purple-400",
  error: "bg-red-500/20 text-red-400",
};

export default function BroadcastDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: bc, isLoading } = useBroadcast(id);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const { data: leadsPage } = useBroadcastLeads(id, {
    limit: 50,
    offset: 0,
    status: statusFilter,
  });

  const activate = useActivateBroadcast();
  const pause = usePauseBroadcast();
  const archive = useArchiveBroadcast();
  const del = useDeleteBroadcast();
  const update = useUpdateBroadcast(id);

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  // Sequence editing
  const [editingSeq, setEditingSeq] = useState(false);
  const [seqMsg1, setSeqMsg1] = useState("");
  const [seqMsg2, setSeqMsg2] = useState("");
  const [seqMsg3, setSeqMsg3] = useState("");
  const [seqDelay, setSeqDelay] = useState(24);
  const [seqRouting, setSeqRouting] = useState<"skip" | "branch">("skip");
  const [seqPriorOnly, setSeqPriorOnly] = useState("");
  const [showSeqMsg2, setShowSeqMsg2] = useState(false);
  const [showSeqMsg3, setShowSeqMsg3] = useState(false);

  function openSeqEditor() {
    setSeqMsg1(bc?.message_1 ?? "");
    setSeqMsg2(bc?.message_2 ?? "");
    setSeqMsg3(bc?.message_3 ?? "");
    setSeqDelay(bc?.delay_between_hours ?? 24);
    setSeqRouting((bc?.conversation_routing ?? "skip") as "skip" | "branch");
    setSeqPriorOnly(bc?.message_prior_only ?? "");
    setShowSeqMsg2(!!bc?.message_2);
    setShowSeqMsg3(!!bc?.message_3);
    setEditingSeq(true);
  }

  function saveSequence() {
    if (!seqMsg1.trim()) { toast.error("Message 1 is required"); return; }
    update.mutate(
      {
        message_1: seqMsg1.trim(),
        message_2: showSeqMsg2 && seqMsg2.trim() ? seqMsg2.trim() : undefined,
        message_3: showSeqMsg2 && showSeqMsg3 && seqMsg3.trim() ? seqMsg3.trim() : undefined,
        delay_between_hours: seqDelay,
        conversation_routing: seqRouting,
        message_prior_only: seqRouting === "branch" && seqPriorOnly.trim() ? seqPriorOnly.trim() : undefined,
      },
      {
        onSuccess: () => { toast.success("Sequence updated"); setEditingSeq(false); },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  if (isLoading || !bc) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const counts = bc.status_counts ?? {};
  const total = bc.total_leads || 0;
  const messages = [bc.message_1, bc.message_2, bc.message_3].filter(Boolean) as string[];

  function saveName() {
    if (!nameDraft.trim() || nameDraft.trim() === bc?.name) {
      setEditingName(false);
      return;
    }
    update.mutate(
      { name: nameDraft.trim() },
      {
        onSuccess: () => {
          toast.success("Renamed");
          setEditingName(false);
        },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {editingName ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                className="text-2xl font-medium bg-transparent border-b border-border focus:outline-none focus:border-foreground pb-1 flex-1"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={saveName}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveName();
                  if (e.key === "Escape") setEditingName(false);
                }}
              />
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <h1
                className="text-2xl font-medium truncate cursor-pointer hover:text-muted-foreground/80"
                onClick={() => {
                  setNameDraft(bc.name);
                  setEditingName(true);
                }}
                title="Click to rename"
              >
                {bc.name}
              </h1>
              <StatusBadge status={bc.status} />
              {bc.archived && (
                <span className="text-xs rounded bg-zinc-500/20 text-zinc-400 px-2 py-0.5">
                  Archived
                </span>
              )}
            </div>
          )}
          <div className="mt-1 text-sm text-muted-foreground">
            {bc.account_name}
            {bc.source_list_name && (
              <>
                <span className="mx-2 text-muted-foreground/40">·</span>
                <Link
                  href={`/lead-lists/${bc.source_list_id}`}
                  className="hover:text-foreground"
                >
                  from {bc.source_list_name}
                </Link>
              </>
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2 shrink-0">
          {bc.status === "draft" || bc.status === "paused" ? (
            <Button
              variant="default"
              onClick={() =>
                activate.mutate(bc.id, {
                  onSuccess: () => toast.success("Activated"),
                  onError: (err: Error) => toast.error(err.message),
                })
              }
            >
              <Play className="mr-2 h-4 w-4" /> Activate
            </Button>
          ) : bc.status === "active" ? (
            <Button
              variant="outline"
              onClick={() =>
                pause.mutate(bc.id, {
                  onSuccess: () => toast.success("Paused"),
                })
              }
            >
              <Pause className="mr-2 h-4 w-4" /> Pause
            </Button>
          ) : null}
          {!bc.archived && (
            <Button
              variant="outline"
              onClick={() =>
                archive.mutate(bc.id, {
                  onSuccess: () => toast.success("Archived"),
                })
              }
            >
              <Archive className="mr-2 h-4 w-4" /> Archive
            </Button>
          )}
          {(bc.status === "draft" || bc.archived) && (
            <Button
              variant="outline"
              onClick={() => {
                if (confirm("Delete this broadcast? Snapshot rows will be removed.")) {
                  del.mutate(bc.id, {
                    onSuccess: () => {
                      toast.success("Deleted");
                      router.push("/broadcasts");
                    },
                    onError: (err: Error) => toast.error(err.message),
                  });
                }
              }}
            >
              <Trash2 className="mr-2 h-4 w-4" /> Delete
            </Button>
          )}
        </div>
      </div>

      {/* Status counts row */}
      <div className="grid gap-3 sm:grid-cols-6">
        {["pending", "sent", "sequence_complete", "skipped", "manual_outreach", "error"].map((s) => {
          const n = counts[s] ?? 0;
          return (
            <button
              key={s}
              onClick={() =>
                setStatusFilter(statusFilter === s ? undefined : s)
              }
              className={`rounded-md border px-3 py-2 text-left transition-colors ${
                statusFilter === s
                  ? "border-foreground bg-muted"
                  : "border-border hover:border-muted-foreground/40"
              }`}
            >
              <div className="text-xs text-muted-foreground">{STATUS_LABEL[s]}</div>
              <div className={`text-lg font-medium ${n > 0 ? "text-foreground" : "text-muted-foreground/40"}`}>
                {n}
              </div>
            </button>
          );
        })}
      </div>

      {/* Messages */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Message sequence</CardTitle>
            {!editingSeq && (
              <Button variant="ghost" size="sm" onClick={openSeqEditor} className="h-7 px-2 text-xs">
                <Pencil className="h-3 w-3 mr-1" /> Edit
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {editingSeq ? (
            /* ── Edit mode ────────────────────────────────────────────── */
            <div className="space-y-4">
              {bc.status === "active" && (
                <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
                  Broadcast is live — changes apply to all pending leads immediately.
                </div>
              )}

              <div className="space-y-1.5">
                <Label className="text-xs">Message 1</Label>
                <MessageTemplateEditor value={seqMsg1} onChange={setSeqMsg1} showCharLimit minHeight={90} />
              </div>

              {showSeqMsg2 ? (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Message 2</Label>
                    <button type="button" onClick={() => { setShowSeqMsg2(false); setShowSeqMsg3(false); setSeqMsg2(""); setSeqMsg3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                  </div>
                  <MessageTemplateEditor value={seqMsg2} onChange={setSeqMsg2} showCharLimit minHeight={80} />
                </div>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqMsg2(true)}>+ Add message 2</Button>
              )}

              {showSeqMsg2 && (showSeqMsg3 ? (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Message 3</Label>
                    <button type="button" onClick={() => { setShowSeqMsg3(false); setSeqMsg3(""); }} className="text-xs text-muted-foreground hover:text-foreground">remove</button>
                  </div>
                  <MessageTemplateEditor value={seqMsg3} onChange={setSeqMsg3} showCharLimit minHeight={80} />
                </div>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => setShowSeqMsg3(true)}>+ Add message 3</Button>
              ))}

              {showSeqMsg2 && (
                <div className="space-y-1.5 max-w-xs">
                  <Label className="text-xs">Delay between messages (hours)</Label>
                  <Input type="number" min={1} max={720} value={seqDelay} onChange={(e) => setSeqDelay(parseInt(e.target.value) || 24)} />
                </div>
              )}

              {/* Conversation routing */}
              <div className="space-y-2 pt-1">
                <Label className="text-xs">If a prior LinkedIn conversation exists</Label>
                <div className="grid grid-cols-2 gap-2">
                  {(["skip", "branch"] as const).map((r) => (
                    <button key={r} type="button" onClick={() => setSeqRouting(r)}
                      className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${seqRouting === r ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground/40 text-muted-foreground"}`}>
                      <div className="font-medium capitalize">{r}</div>
                      <div className="text-xs opacity-70">{r === "skip" ? "Skip anyone already messaged" : "Route per conversation state"}</div>
                    </button>
                  ))}
                </div>
                {seqRouting === "branch" && (
                  <div className="rounded-md border border-border/60 bg-muted/20 p-3 space-y-3">
                    <div className="grid gap-1 text-xs text-muted-foreground">
                      <div className="flex items-center gap-2"><span className="rounded bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 font-medium">Fresh</span><span>No prior messages → msg 1</span></div>
                      <div className="flex items-center gap-2"><span className="rounded bg-blue-500/20 text-blue-400 px-1.5 py-0.5 font-medium">Sent, no reply</span><span>→ warm message below</span></div>
                      <div className="flex items-center gap-2"><span className="rounded bg-amber-500/20 text-amber-400 px-1.5 py-0.5 font-medium">They replied</span><span>→ manual outreach</span></div>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">Warm message (sent if no reply yet)</Label>
                      <MessageTemplateEditor value={seqPriorOnly} onChange={setSeqPriorOnly} placeholder="Hey {{first_name}}, just following up…" showCharLimit minHeight={80} />
                      <p className="text-xs text-muted-foreground">Leave blank to skip &quot;sent, no reply&quot; leads.</p>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex gap-2 pt-1">
                <Button size="sm" onClick={saveSequence} disabled={update.isPending}>{update.isPending ? "Saving…" : "Save changes"}</Button>
                <Button size="sm" variant="outline" onClick={() => setEditingSeq(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            /* ── Read-only view ───────────────────────────────────────── */
            <>
              {messages.map((m, i) => (
                <div key={i} className="rounded-md border border-border/50 bg-muted/20 p-3">
                  <div className="text-xs text-muted-foreground mb-1">
                    Message {i + 1}
                    {i > 0 && <span className="ml-2">· sent {bc.delay_between_hours}h after msg {i}</span>}
                  </div>
                  <div className="whitespace-pre-wrap text-sm">{m}</div>
                </div>
              ))}
              {bc.conversation_routing === "branch" && (
                <div className="mt-2 space-y-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded bg-primary/20 text-primary px-1.5 py-0.5 font-medium">Branch mode</span>
                    <span>Fresh → msg 1 · Replied → manual outreach</span>
                  </div>
                  {bc.message_prior_only ? (
                    <div className="rounded-md border border-purple-500/20 bg-purple-500/5 p-3">
                      <div className="text-xs text-muted-foreground mb-1">If previously messaged, no reply</div>
                      <div className="whitespace-pre-wrap text-sm">{bc.message_prior_only}</div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">No warm message — &quot;sent, no reply&quot; leads will be skipped.</p>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Lead table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">
              Leads {statusFilter && <span className="text-sm text-muted-foreground">· filtered: {STATUS_LABEL[statusFilter]}</span>}
            </CardTitle>
            <Tabs
              value={statusFilter ?? "all"}
              onValueChange={(v) =>
                setStatusFilter(v === "all" ? undefined : v)
              }
            >
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="pending">Pending</TabsTrigger>
                <TabsTrigger value="sent">Sent</TabsTrigger>
                <TabsTrigger value="skipped">Skipped</TabsTrigger>
                <TabsTrigger value="manual_outreach">Manual</TabsTrigger>
                <TabsTrigger value="error">Error</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </CardHeader>
        <CardContent>
          {!leadsPage ? (
            <Skeleton className="h-40" />
          ) : leadsPage.items.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No leads {statusFilter ? `in ${STATUS_LABEL[statusFilter]}` : "yet"}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground border-b border-border">
                    <th className="pb-2 pr-4">Lead</th>
                    <th className="pb-2 pr-4">Company</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2 pr-4">Msg</th>
                    <th className="pb-2">Last sent</th>
                  </tr>
                </thead>
                <tbody>
                  {leadsPage.items.map((bl) => (
                    <tr key={bl.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-4">
                        {bl.lead_linkedin_url ? (
                          <Link
                            href={`/leads/${bl.lead_id}`}
                            className="hover:text-muted-foreground"
                          >
                            {bl.lead_name ?? "(no name)"}
                          </Link>
                        ) : (
                          bl.lead_name ?? "(no name)"
                        )}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {bl.lead_company ?? "—"}
                      </td>
                      <td className="py-2 pr-4">
                        <span
                          className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${
                            STATUS_STYLE[bl.status] ?? "bg-muted text-muted-foreground"
                          }`}
                        >
                          {STATUS_LABEL[bl.status] ?? bl.status}
                        </span>
                        {bl.skipped_reason && (
                          <span className="ml-1 text-xs text-muted-foreground">
                            {bl.skipped_reason}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {bl.last_message_index ? `${bl.last_message_index}/${messages.length}` : "—"}
                      </td>
                      <td className="py-2 text-muted-foreground text-xs">
                        {bl.last_message_sent_at
                          ? new Date(bl.last_message_sent_at.endsWith("Z") ? bl.last_message_sent_at : bl.last_message_sent_at + "Z").toLocaleString(undefined, {
                              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                            })
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {leadsPage.total > leadsPage.items.length && (
                <p className="mt-3 text-xs text-muted-foreground text-center">
                  Showing {leadsPage.items.length} of {leadsPage.total}. Pagination coming soon.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Footer stats */}
      <div className="text-xs text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
        <span>Total leads: {total}</span>
        <span>Messages sent: {bc.messages_sent ?? 0}</span>
        <span>Delay: {bc.delay_between_hours}h between messages</span>
        <span>Created: {new Date(bc.created_at.endsWith("Z") ? bc.created_at : bc.created_at + "Z").toLocaleDateString()}</span>
      </div>
    </div>
  );
}
