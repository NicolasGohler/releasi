"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const statusColors: Record<string, string> = {
  active: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  connected: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  success: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  paused: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  scheduled: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  connection_requested: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  pending: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  draft: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  error: "bg-red-500/15 text-red-400 border-red-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  skipped: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  invalid: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  limit_paused: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  suspended: "bg-red-500/15 text-red-400 border-red-500/30",
  cookie_expired: "bg-red-500/15 text-red-400 border-red-500/30",
};

// Display labels — overrides the default underscore→space transform.
const statusLabels: Record<string, string> = {
  connection_requested: "requested",
  limit_paused: "limit paused",
  cookie_expired: "session expired",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("text-[11px] font-medium", statusColors[status] ?? "")}
    >
      {statusLabels[status] ?? status.replace(/_/g, " ")}
    </Badge>
  );
}
