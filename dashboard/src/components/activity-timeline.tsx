"use client";

import type { ActionLog } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";

function formatTime(iso: string, tz?: string | null) {
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString("en-US", {
    timeZone: tz ?? undefined,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatActionType(type: string) {
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ActivityTimeline({ logs, timezone }: { logs: ActionLog[]; timezone?: string | null }) {
  if (logs.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8 text-center">
        No activity yet
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {logs.map((log) => (
        <div
          key={log.id}
          className="flex items-start gap-3 rounded-md border border-border p-3 text-sm"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium">{formatActionType(log.action_type)}</span>
              <StatusBadge status={log.status} />
            </div>
            {log.details && (
              <p className="text-xs text-muted-foreground mt-1 truncate">
                {JSON.stringify(log.details)}
              </p>
            )}
          </div>
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {formatTime(log.created_at, timezone)}
          </span>
        </div>
      ))}
    </div>
  );
}
