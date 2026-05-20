"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  useLead,
  useUpdateLead,
  useLeadActivity,
  useSkipLead,
  useRequeueLead,
  useDeleteLead,
  useRestoreLead,
  useFindTelegram,
  useFindTelegramStatus,
} from "@/hooks/use-queries";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import type { Lead, LeadActivity, FindTelegramTask } from "@/lib/types";
import {
  ArrowLeft, ExternalLink, Pencil, Check, X,
  Copy, Send, RotateCcw, FastForward, Trash2, Undo2,
  Clock, Zap, MessageSquare, UserCheck, AlertCircle,
  Link2, Loader2, Search,
} from "lucide-react";

// ── helpers ──────────────────────────────────────────────────────────────────

function relativeDate(dateStr: string): { label: string; full: string } {
  const d = new Date(dateStr.endsWith("Z") ? dateStr : dateStr + "Z");
  const diffMs = Date.now() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  const label =
    diffDays === 0 ? "today" :
    diffDays === 1 ? "yesterday" :
    diffDays < 7 ? `${diffDays}d ago` :
    diffDays < 30 ? `${Math.floor(diffDays / 7)}w ago` :
    `${Math.floor(diffDays / 30)}mo ago`;
  const full = d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return { label, full };
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.736-8.849L1.254 2.25H8.08l4.253 5.622L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77z" />
    </svg>
  );
}

const ACTION_ICON: Record<string, React.ReactNode> = {
  connection_request: <UserCheck className="h-3.5 w-3.5" />,
  followup_message: <MessageSquare className="h-3.5 w-3.5" />,
  check_acceptance: <UserCheck className="h-3.5 w-3.5" />,
  acceptance_check_summary: <UserCheck className="h-3.5 w-3.5" />,
  invitation_withdrawn: <X className="h-3.5 w-3.5" />,
  error: <AlertCircle className="h-3.5 w-3.5" />,
  feed_view: <Zap className="h-3.5 w-3.5" />,
  profile_view: <Zap className="h-3.5 w-3.5" />,
};

const ACTION_COLOUR: Record<string, string> = {
  success: "text-emerald-500 bg-emerald-500/10 border-emerald-500/20",
  failed: "text-rose-500 bg-rose-500/10 border-rose-500/20",
  skipped: "text-amber-500 bg-amber-500/10 border-amber-500/20",
};

// ── Inline editable field ────────────────────────────────────────────────────

interface InlineFieldProps {
  label: string;
  value: string | null | undefined;
  onSave: (val: string | null) => Promise<void>;
  placeholder?: string;
  prefix?: string;
  hint?: string;
  transform?: (raw: string) => string;
}

function InlineField({
  label, value, onSave, placeholder = "—", prefix, hint, transform,
}: InlineFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function startEdit() {
    setDraft(value ?? "");
    setEditing(true);
  }

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function handleSave() {
    setSaving(true);
    try {
      const cleaned = draft.trim().replace(/^@/, "");
      await onSave(cleaned || null);
      setEditing(false);
    } catch {
      toast.error(`Failed to save ${label.toLowerCase()}`);
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(false);
  }

  const displayValue = value ? (transform ? transform(value) : value) : null;

  return (
    <div className="group flex flex-col gap-0.5 py-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {editing ? (
        <div className="flex items-center gap-1.5">
          {prefix && <span className="text-sm text-muted-foreground">{prefix}</span>}
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-ring"
            disabled={saving}
          />
          <button onClick={handleSave} disabled={saving}
            className="rounded p-1 text-emerald-500 hover:bg-emerald-500/10 transition-colors">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setEditing(false)}
            className="rounded p-1 text-muted-foreground hover:bg-muted transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 min-h-[1.75rem]">
          <span className={`text-sm ${displayValue ? "" : "text-muted-foreground/50 italic"}`}>
            {displayValue ? (prefix ? `${prefix}${displayValue}` : displayValue) : placeholder}
          </span>
          <button onClick={startEdit}
            className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-all"
            title={`Edit ${label.toLowerCase()}`}>
            <Pencil className="h-3 w-3" />
          </button>
          {hint && displayValue && (
            <span className="text-xs text-muted-foreground/50">{hint}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Telegram section (prominent inline-edit + find button + candidates) ───────

interface TelegramSectionProps {
  lead: Lead;
  onSave: (v: string | null) => Promise<void>;
}

function TelegramSection({ lead, onSave }: TelegramSectionProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const findMutation = useFindTelegram(lead.id);
  const taskQuery = useFindTelegramStatus(lead.id, taskId);

  const taskData = taskQuery.data as FindTelegramTask | undefined;
  const isSearching = !!taskId && taskData?.status === "running";
  const searchDone = !!taskId && (taskData?.status === "done" || taskData?.status === "error");

  // Candidates to show: from active task result, or from DB-persisted alternatives
  const liveCandidates: string[] = taskId && taskData
    ? [
        ...(taskData.telegram_username ? [taskData.telegram_username] : []),
        ...(taskData.telegram_alternatives ?? []),
      ]
    : [];
  const storedCandidates: string[] = !taskId && lead.telegram_alternatives
    ? lead.telegram_alternatives
    : [];
  const candidates = taskId ? liveCandidates : storedCandidates;

  // Auto-open edit field with best match when search completes
  useEffect(() => {
    if (taskData?.status === "done" && taskData.telegram_username && !editing) {
      setDraft(taskData.telegram_username);
      setEditing(true);
    }
  }, [taskData?.status, taskData?.telegram_username]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function startEdit() {
    setDraft(lead.telegram_username ?? "");
    setEditing(true);
  }

  function pickCandidate(handle: string) {
    setDraft(handle);
    setEditing(true);
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const cleaned = draft.trim().replace(/^@/, "");
      await onSave(cleaned || null);
      setEditing(false);
      setTaskId(null); // clear search state after save
    } catch {
      toast.error("Failed to save Telegram username");
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") setEditing(false);
  }

  async function handleFind() {
    setTaskId(null);
    try {
      const result = await findMutation.mutateAsync();
      setTaskId(result.task_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start search";
      toast.error(msg);
    }
  }

  const displayValue = lead.telegram_username;

  return (
    <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 px-3 py-2.5 space-y-2">
      {/* Label row */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-sky-600 dark:text-sky-400">Telegram</span>
        <button
          onClick={handleFind}
          disabled={findMutation.isPending || isSearching}
          className="flex items-center gap-1 text-xs text-sky-600 dark:text-sky-400 hover:text-sky-500 disabled:opacity-40 transition-colors"
          title="Search Telegram for this lead"
        >
          {isSearching ? (
            <><Loader2 className="h-3 w-3 animate-spin" /> Searching…</>
          ) : (
            <><Search className="h-3 w-3" /> Find</>
          )}
        </button>
      </div>

      {/* Editable field */}
      {editing ? (
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground">@</span>
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="username"
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-sky-400"
            disabled={saving}
          />
          <button onClick={handleSave} disabled={saving}
            className="rounded p-1 text-emerald-500 hover:bg-emerald-500/10 transition-colors">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button onClick={() => setEditing(false)}
            className="rounded p-1 text-muted-foreground hover:bg-muted transition-colors">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="group flex items-center gap-1.5 min-h-[1.75rem]">
          {displayValue ? (
            <>
              <span className="text-sm font-medium">@{displayValue}</span>
              <a
                href={`https://t.me/${displayValue}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sky-500 hover:text-sky-400 transition-colors"
                title="Open in Telegram"
              >
                <ExternalLink className="h-3 w-3" />
              </a>
              <CopyButton text={displayValue} label="Copy handle" />
            </>
          ) : (
            <span className="text-sm text-muted-foreground/50 italic">
              {isSearching ? "Searching…" : "No handle yet"}
            </span>
          )}
          <button onClick={startEdit}
            className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-all ml-0.5"
            title="Edit Telegram handle">
            <Pencil className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Candidates picker */}
      {candidates.length > 0 && (
        <div className="pt-1 border-t border-sky-500/20 space-y-1.5">
          <p className="text-xs text-sky-600/70 dark:text-sky-400/70">
            {taskId ? "Found — pick the right one:" : "Saved candidates:"}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {candidates.map((handle) => (
              <button
                key={handle}
                onClick={() => pickCandidate(handle)}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors
                  ${lead.telegram_username === handle
                    ? "border-sky-500 bg-sky-500/20 text-sky-700 dark:text-sky-300"
                    : "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400 hover:bg-sky-500/20 hover:border-sky-500/50"
                  }`}
              >
                @{handle}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search logs (collapsed by default) */}
      {searchDone && taskData?.logs && taskData.logs.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground/60 hover:text-muted-foreground select-none">
            Search log ({taskData.logs.length} lines)
          </summary>
          <div className="mt-1 rounded bg-muted/50 p-2 font-mono text-xs leading-relaxed max-h-40 overflow-y-auto space-y-0.5">
            {taskData.logs.map((line, i) => (
              <p key={i} className="text-muted-foreground">{line}</p>
            ))}
          </div>
        </details>
      )}

      {/* Error state */}
      {taskData?.status === "error" && (
        <p className="text-xs text-rose-500">{taskData.error || "Search failed"}</p>
      )}
    </div>
  );
}

// ── Copy button ──────────────────────────────────────────────────────────────

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
      className="rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground transition-colors"
      title={label ?? "Copy"}
    >
      {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

// ── Activity timeline item ───────────────────────────────────────────────────

function ActivityItem({ entry }: { entry: LeadActivity }) {
  const { label, full } = relativeDate(entry.created_at);
  const colour = ACTION_COLOUR[entry.status] ?? ACTION_COLOUR.skipped;
  const icon = ACTION_ICON[entry.action_type] ?? <Zap className="h-3.5 w-3.5" />;
  const label_ = entry.action_type.replace(/_/g, " ");
  const details = entry.details as { reason?: string; url?: string } | null;

  return (
    <div className="flex gap-3 items-start py-2 border-b last:border-0">
      <div className={`mt-0.5 flex-shrink-0 rounded-full border p-1 ${colour}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-1.5 flex-wrap">
          <span className="text-sm font-medium capitalize">{label_}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full border font-medium ${colour}`}>
            {entry.status}
          </span>
        </div>
        {details?.reason && (
          <p className="text-xs text-muted-foreground mt-0.5">
            {details.reason.replace(/_/g, " ")}
          </p>
        )}
        {entry.account_name && (
          <p className="text-xs text-muted-foreground/60 mt-0.5">via {entry.account_name}</p>
        )}
      </div>
      <span className="text-xs text-muted-foreground/60 whitespace-nowrap" title={full}>{label}</span>
    </div>
  );
}

// ── Milestone row ────────────────────────────────────────────────────────────

function MilestoneRow({ icon, label, dateStr }: { icon: React.ReactNode; label: string; dateStr: string | null | undefined }) {
  if (!dateStr) return null;
  const { label: rel, full } = relativeDate(dateStr);
  return (
    <div className="flex items-center gap-2 py-1.5 border-b last:border-0">
      <span className="text-muted-foreground/60">{icon}</span>
      <span className="text-sm flex-1">{label}</span>
      <span className="text-xs text-muted-foreground" title={full}>{rel}</span>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function LeadDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const { data: lead, isLoading } = useLead(id);
  const { data: activity, isLoading: activityLoading } = useLeadActivity(id);
  const update = useUpdateLead(id);
  const skip = useSkipLead();
  const requeue = useRequeueLead();
  const remove = useDeleteLead();
  const restore = useRestoreLead();

  const save = useCallback(async (field: string, value: string | null) => {
    await update.mutateAsync({ [field]: value });
    toast.success("Saved");
  }, [update]);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!lead) {
    return (
      <div className="p-6 text-center">
        <p className="text-muted-foreground">Lead not found</p>
        <Link href="/leads" className="text-sm text-primary hover:underline mt-2 inline-block">← Back to leads</Link>
      </div>
    );
  }

  const displayName = [lead.first_name, lead.last_name].filter(Boolean).join(" ") || "Unnamed lead";
  const canSkip = lead.status === "pending" || lead.status === "scheduled";
  const canRequeue = lead.status === "error" || lead.status === "skipped" || lead.status === "withdrawn";
  const isRemoved = lead.status === "removed";

  const twitterHandle = lead.twitter_url
    ? lead.twitter_url.replace(/.*x\.com\//, "").replace(/.*twitter\.com\//, "")
    : null;

  return (
    <div className="max-w-5xl mx-auto px-6 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <Link
          href="/leads"
          className="mt-1 flex-shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-muted transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-semibold tracking-tight truncate">{displayName}</h1>
            <StatusBadge status={lead.status} />
          </div>
          {lead.title && (
            <p className="text-sm text-muted-foreground mt-0.5">
              {lead.title}{lead.company ? ` · ${lead.company}` : ""}
            </p>
          )}
        </div>

        {/* Quick links */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <a
            href={lead.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md p-2 text-muted-foreground hover:bg-muted transition-colors"
            title="Open LinkedIn profile"
          >
            <Link2 className="h-4 w-4" />
          </a>
          {lead.twitter_url && (
            <a
              href={lead.twitter_url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-2 text-muted-foreground hover:bg-muted transition-colors"
              title={`X: @${twitterHandle}`}
            >
              <XIcon className="h-4 w-4" />
            </a>
          )}
          {lead.telegram_username && (
            <a
              href={`https://t.me/${lead.telegram_username}`}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-2 text-sky-500 hover:bg-sky-500/10 transition-colors"
              title={`Telegram: @${lead.telegram_username}`}
            >
              <Send className="h-4 w-4" />
            </a>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {canSkip && (
            <Button size="sm" variant="outline" disabled={skip.isPending}
              onClick={() => skip.mutate(lead.id, { onSuccess: () => toast.success("Lead skipped") })}>
              <FastForward className="mr-1.5 h-3.5 w-3.5" /> Skip
            </Button>
          )}
          {canRequeue && (
            <Button size="sm" variant="outline" disabled={requeue.isPending}
              onClick={() => requeue.mutate(lead.id, { onSuccess: () => toast.success("Lead re-queued") })}>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Re-queue
            </Button>
          )}
          {isRemoved ? (
            <Button size="sm" variant="outline" disabled={restore.isPending}
              onClick={() => restore.mutate(lead.id, { onSuccess: () => toast.success("Lead restored") })}>
              <Undo2 className="mr-1.5 h-3.5 w-3.5" /> Restore
            </Button>
          ) : (
            <Button size="sm" variant="ghost"
              className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
              disabled={remove.isPending}
              onClick={() => remove.mutate(lead.id, {
                onSuccess: () => { toast.success("Lead removed"); router.push("/leads"); }
              })}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: editable profile */}
        <div className="lg:col-span-2 space-y-4">

          {/* Profile card */}
          <div className="rounded-xl border bg-card p-4 space-y-1">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Profile</h2>
            <div className="grid grid-cols-2 gap-x-6">
              <InlineField label="First name" value={lead.first_name} placeholder="Add first name"
                onSave={(v) => save("first_name", v)} />
              <InlineField label="Last name" value={lead.last_name} placeholder="Add last name"
                onSave={(v) => save("last_name", v)} />
            </div>
            <InlineField label="Title / Role" value={lead.title} placeholder="Add title"
              onSave={(v) => save("title", v)} />
            <InlineField label="Company" value={lead.company} placeholder="Add company"
              onSave={(v) => save("company", v)} />
            <div className="flex items-start gap-2">
              <div className="flex-1">
                <InlineField label="Email" value={lead.email} placeholder="Add email"
                  onSave={(v) => save("email", v)} />
              </div>
              {lead.email && (
                <div className="mt-6">
                  <CopyButton text={lead.email} label="Copy email" />
                </div>
              )}
            </div>
            <InlineField label="Phone" value={lead.phone} placeholder="Add phone"
              onSave={(v) => save("phone", v)} />
          </div>

          {/* Social card */}
          <div className="rounded-xl border bg-card p-4 space-y-1">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Social & Links</h2>

            {/* LinkedIn — read-only */}
            <div className="flex flex-col gap-0.5 py-1.5">
              <span className="text-xs font-medium text-muted-foreground">LinkedIn</span>
              <div className="flex items-center gap-2">
                <a
                  href={lead.linkedin_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline flex items-center gap-1 truncate"
                >
                  {lead.linkedin_url.replace("https://www.", "")}
                  <ExternalLink className="h-3 w-3 flex-shrink-0" />
                </a>
                <CopyButton text={lead.linkedin_url} label="Copy LinkedIn URL" />
              </div>
            </div>

            {/* Twitter / X */}
            <InlineField
              label="X / Twitter"
              value={twitterHandle}
              placeholder="Add X handle"
              prefix="@"
              hint="opens x.com"
              onSave={async (v) => {
                const url = v ? `https://x.com/${v.replace(/^@/, "")}` : null;
                await save("twitter_url", url);
              }}
            />

            {/* Telegram — prominent with Find button */}
            <TelegramSection
              lead={lead}
              onSave={(v) => save("telegram_username", v)}
            />
          </div>

          {/* Extra data if any */}
          {lead.extra_data && Object.keys(lead.extra_data).length > 0 && (
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Additional Data</h2>
              <div className="space-y-1.5">
                {Object.entries(lead.extra_data).map(([k, v]) => (
                  <div key={k} className="flex gap-2 text-sm">
                    <span className="text-muted-foreground min-w-[120px] capitalize">{k.replace(/_/g, " ")}</span>
                    <span className="text-foreground break-all">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: status + activity */}
        <div className="space-y-4">

          {/* Status & timestamps card */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Campaign Status</h2>
            {lead.campaign_name && (
              <p className="text-sm font-medium mb-1">{lead.campaign_name}</p>
            )}
            {lead.lead_list_name && (
              <p className="text-xs text-muted-foreground mb-3">List: {lead.lead_list_name}</p>
            )}
            <div className="mb-3">
              <StatusBadge status={lead.status} />
            </div>
            {lead.error_message && (
              <div className="rounded-md bg-rose-500/10 border border-rose-500/20 px-3 py-2 mb-3">
                <p className="text-xs text-rose-600 dark:text-rose-400 font-medium">Error</p>
                <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-0.5">
                  {lead.error_message.replace(/_/g, " ")}
                </p>
                {lead.retry_count > 0 && (
                  <p className="text-xs text-rose-600/60 mt-0.5">{lead.retry_count} retry attempt{lead.retry_count !== 1 ? "s" : ""}</p>
                )}
              </div>
            )}

            <div className="space-y-0">
              <MilestoneRow icon={<Clock className="h-3.5 w-3.5" />} label="Imported" dateStr={lead.created_at} />
              <MilestoneRow icon={<UserCheck className="h-3.5 w-3.5" />} label="Connection requested" dateStr={lead.connection_requested_at} />
              <MilestoneRow icon={<UserCheck className="h-3.5 w-3.5" />} label="Connection accepted" dateStr={lead.connection_accepted_at} />
              <MilestoneRow icon={<MessageSquare className="h-3.5 w-3.5" />} label="Follow-up sent" dateStr={lead.followup_sent_at} />
              {lead.scheduled_at && lead.status === "scheduled" && (
                <MilestoneRow icon={<Clock className="h-3.5 w-3.5" />} label="Scheduled for" dateStr={lead.scheduled_at} />
              )}
              {lead.updated_at && (
                <div className="pt-2 mt-1 border-t">
                  <p className="text-xs text-muted-foreground/50">
                    Last updated {relativeDate(lead.updated_at).label}
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Activity log */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Activity</h2>
            {activityLoading ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            ) : !activity || activity.length === 0 ? (
              <p className="text-sm text-muted-foreground/60 text-center py-4">No activity yet</p>
            ) : (
              <div className="max-h-80 overflow-y-auto -mx-1 px-1">
                {activity.map((entry) => (
                  <ActivityItem key={entry.id} entry={entry} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
