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

  // Collapse all CHECK_ACCEPTANCE entries into a single summary row
  const acceptanceEntries = logs.filter((l) => l.action_type.toUpperCase() === "CHECK_ACCEPTANCE");
  const acceptanceCount = acceptanceEntries.length;
  const latestAcceptance = acceptanceEntries[0]; // logs are newest-first

  const displayLogs: Array<ActionLog | { _type: "acceptance_summary"; count: number; entry: ActionLog }> = [];
  let acceptanceSummarized = false;

  for (const log of logs) {
    if (log.action_type.toUpperCase() === "CHECK_ACCEPTANCE") {
      if (!acceptanceSummarized) {
        displayLogs.push({ _type: "acceptance_summary", count: acceptanceCount, entry: latestAcceptance });
        acceptanceSummarized = true;
      }
      // Skip remaining individual acceptance entries
    } else {
      displayLogs.push(log);
    }
  }

  return (
    <div className="space-y-3">
      {displayLogs.map((item, idx) => {
        // Collapsed acceptance summary row
        if ("_type" in item && item._type === "acceptance_summary") {
          return (
            <div
              key="acceptance-summary"
              className="flex items-start gap-3 rounded-md border border-border p-3 text-sm"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">Check Acceptance</span>
                  <StatusBadge status="SUCCESS" />
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {item.count} connection{item.count !== 1 ? "s" : ""} accepted
                </p>
              </div>
              <span className="text-xs text-muted-foreground whitespace-nowrap">
                {formatTime(item.entry.created_at, timezone)}
              </span>
            </div>
          );
        }

        const log = item as ActionLog;
        const isConnectionRequest = log.action_type.toUpperCase() === "CONNECTION_REQUEST";
        const leadName =
          log.lead_first_name || log.lead_last_name
            ? [log.lead_first_name, log.lead_last_name].filter(Boolean).join(" ")
            : null;

        return (
          <div
            key={log.id}
            className="flex items-start gap-3 rounded-md border border-border p-3 text-sm"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{formatActionType(log.action_type)}</span>
                {isConnectionRequest && leadName ? (
                  <>
                    <span className="text-muted-foreground">-</span>
                    {log.lead_url ? (
                      <a
                        href={log.lead_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-500 hover:underline truncate"
                      >
                        {leadName}
                      </a>
                    ) : (
                      <span className="text-muted-foreground truncate">{leadName}</span>
                    )}
                  </>
                ) : null}
                <StatusBadge status={log.status} />
              </div>
              {isConnectionRequest && log.details && (
                <p className="text-xs text-muted-foreground mt-1 truncate">
                  {(log.details as Record<string, unknown>).reason === "already_connected"
                    ? "Already connected"
                    : JSON.stringify(log.details)}
                </p>
              )}
              {!isConnectionRequest && log.details && (
                <p className="text-xs text-muted-foreground mt-1 truncate">
                  {JSON.stringify(log.details)}
                </p>
              )}
            </div>
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              {formatTime(log.created_at, timezone)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
