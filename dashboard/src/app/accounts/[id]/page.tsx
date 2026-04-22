"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import {
  useAccount,
  useAccountStats,
  useAccountActivity,
  useAccountSchedule,
  useAccountHealth,
  useUpdateAccount,
  useUpdateCookie,
  useArchiveAccount,
} from "@/hooks/use-queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { DailyChart } from "@/components/stats/daily-chart";
import { ActivityTimeline } from "@/components/activity-timeline";
import { LocalTimeCard } from "@/components/accounts/local-time-card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { startLoginSession, finishLoginSession, cancelLoginSession, getAvatarUrl, checkConnection, replanAccount } from "@/lib/api";
import { ProxySettings, emptyProxyForm, type ProxyFormValue } from "@/components/proxy-settings";

export default function AccountDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: account, isLoading } = useAccount(id);
  const { data: stats } = useAccountStats(id);
  const { data: activity } = useAccountActivity(id);
  const { data: schedule } = useAccountSchedule(id);
  const { data: health } = useAccountHealth(id);
  const updateAccount = useUpdateAccount(id);
  const updateCookie = useUpdateCookie(id);
  const archiveAccount = useArchiveAccount();
  const [newCookie, setNewCookie] = useState("");
  const [editName, setEditName] = useState("");
  const [editTimezone, setEditTimezone] = useState("");
  const [editDailyLimit, setEditDailyLimit] = useState("");
  const [editWeeklyLimit, setEditWeeklyLimit] = useState("");
  const [settingsInitialized, setSettingsInitialized] = useState(false);
  const [loginSessionActive, setLoginSessionActive] = useState(false);
  const [editWithdrawThreshold, setEditWithdrawThreshold] = useState("");
  const [proxyForm, setProxyForm] = useState<ProxyFormValue>(emptyProxyForm);
  const [passwordEditing, setPasswordEditing] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [connectionChecking, setConnectionChecking] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{
    valid: boolean;
    reason?: string;
    error?: string;
    elapsed_ms?: number;
  } | null>(null);

  if (account && !settingsInitialized) {
    setEditName(account.name);
    setEditTimezone(account.timezone ?? "");
    setEditDailyLimit(String(account.daily_limit));
    setEditWeeklyLimit(String(account.weekly_limit));
    setEditWithdrawThreshold(account.withdraw_threshold ? String(account.withdraw_threshold) : "");
    setProxyForm({
      host: account.proxy_host ?? "",
      port: account.proxy_port != null ? String(account.proxy_port) : "",
      username: account.proxy_username ?? "",
      password: "",
      country: account.proxy_country ?? "",
    });
    setPasswordEditing(false);
    setSettingsInitialized(true);
  }

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

  if (!account) {
    return <p className="text-muted-foreground">Account not found</p>;
  }

  const totalSent = stats?.reduce((a, s) => a + s.connection_requests_sent, 0) ?? 0;
  const totalAccepted = stats?.reduce((a, s) => a + s.connections_accepted, 0) ?? 0;
  const totalErrors = stats?.reduce((a, s) => a + s.errors, 0) ?? 0;
  const acceptRate = totalSent > 0 ? Math.round((totalAccepted / totalSent) * 100) : 0;

  function handleSaveSettings() {
    const data: Record<string, unknown> = {};
    if (editName !== account!.name) data.name = editName;
    if (editTimezone !== (account!.timezone ?? "")) data.timezone = editTimezone;
    if (Number(editDailyLimit) !== account!.daily_limit) data.daily_limit = Number(editDailyLimit);
    if (Number(editWeeklyLimit) !== account!.weekly_limit) data.weekly_limit = Number(editWeeklyLimit);
    const newThreshold = editWithdrawThreshold ? Number(editWithdrawThreshold) : null;
    if (newThreshold !== (account!.withdraw_threshold ?? null)) data.withdraw_threshold = newThreshold;
    const newCountry = proxyForm.country.trim() || null;
    if (newCountry !== (account!.proxy_country ?? null)) data.proxy_country = newCountry;

    const newHost = proxyForm.host.trim() || null;
    const newPort = proxyForm.port ? Number(proxyForm.port) : null;
    const newUsername = proxyForm.username.trim() || null;
    if (newHost !== (account!.proxy_host ?? null)) data.proxy_host = newHost;
    if (newPort !== (account!.proxy_port ?? null)) data.proxy_port = newPort;
    if (newUsername !== (account!.proxy_username ?? null)) data.proxy_username = newUsername;
    // Only send password when user explicitly rotated it (Change clicked)
    // or when this is a fresh entry (no stored password).
    if (passwordEditing || !account!.proxy_password_set) {
      data.proxy_password = proxyForm.password || null;
    }
    if (Object.keys(data).length === 0) {
      toast.info("No changes to save");
      return;
    }
    const needsReplan = "daily_limit" in data || "weekly_limit" in data || "timezone" in data;
    updateAccount.mutate(data as Parameters<typeof updateAccount.mutate>[0], {
      onSuccess: () => {
        if (needsReplan) {
          toast.promise(replanAccount(id), {
            loading: "Regenerating today's plan…",
            success: (res) => `Settings saved · ${res.scheduled} leads rescheduled`,
            error: "Settings saved, but replanning failed",
          });
        } else {
          toast.success("Account settings saved");
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
            <div className="h-9 w-9 shrink-0 overflow-hidden rounded-full bg-muted">
              <img
                src={getAvatarUrl(id)}
                alt=""
                className="h-full w-full object-cover"
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
              />
            </div>
            <h1 className="text-lg font-semibold truncate">{account.name}</h1>
            <StatusBadge status={account.status} />
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveSettings}
              disabled={updateAccount.isPending}
            >
              Save Settings
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (confirm("Archive this account? It will be paused and hidden from the main view.")) {
                  archiveAccount.mutate(id, {
                    onSuccess: () => {
                      toast.success("Account archived");
                      router.push("/accounts");
                    },
                    onError: (err) => toast.error(err.message),
                  });
                }
              }}
              disabled={archiveAccount.isPending}
            >
              Archive
            </Button>
          </div>
        </div>
      </div>

      <div className="space-y-6">
        {account.status === "cookie_expired" && (
          <div className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3">
            <p className="text-sm text-red-400">
              Login required — use the <span className="font-medium">Manual Login</span> button in the Settings tab to authenticate with LinkedIn.
            </p>
          </div>
        )}

        {account.paused_until && new Date(account.paused_until) > new Date() && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3">
            <p className="text-sm text-amber-400">
              Account paused due to weekly limit — resumes{" "}
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

        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          <StatCard label="Sent (30d)" value={totalSent} />
          <StatCard label="Accepted" value={totalAccepted} sub={`${acceptRate}% rate`} />
          <StatCard label="Errors" value={totalErrors} />
          <StatCard label="Pending Requests" value={account.pending_requests ?? 0} sub={account.pending_requests != null && account.pending_requests > 1000 ? "Over 1K — risk of limits" : undefined} />
          <StatCard label="Daily Target" value={account.daily_limit} />
        </div>

        <Tabs defaultValue={account.status === "cookie_expired" ? "settings" : "stats"}>
          <TabsList>
            <TabsTrigger value="stats">Stats</TabsTrigger>
            <TabsTrigger value="schedule">Schedule</TabsTrigger>
            <TabsTrigger value="health">Health</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          <TabsContent value="stats" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>Daily Activity (30 days)</CardTitle>
              </CardHeader>
              <CardContent>
                <DailyChart data={stats ?? []} />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="schedule" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>
                  Today&apos;s Schedule
                  {schedule && (
                    <span className="ml-2 text-sm font-normal text-muted-foreground">
                      {schedule.filter((s) => s.status === "sent").length} of {schedule.length} executed
                    </span>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {!schedule || schedule.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4">No leads scheduled for today.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                          <th className="pb-2 font-medium">Time</th>
                          <th className="pb-2 font-medium">Lead</th>
                          <th className="pb-2 font-medium">Campaign</th>
                          <th className="pb-2 font-medium text-right">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {schedule.map((slot) => (
                          <tr key={slot.lead_id} className="border-b border-border/50 last:border-0">
                            <td className="py-2 text-muted-foreground tabular-nums">
                              {slot.scheduled_at
                                ? new Date(slot.scheduled_at.endsWith("Z") ? slot.scheduled_at : slot.scheduled_at + "Z").toLocaleTimeString(undefined, {
                                    timeZone: account.timezone ?? undefined,
                                    hour: "2-digit",
                                    minute: "2-digit",
                                  })
                                : "—"}
                            </td>
                            <td className="py-2">
                              <a
                                href={slot.linkedin_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="font-medium hover:underline"
                              >
                                {[slot.first_name, slot.last_name].filter(Boolean).join(" ") || "Unknown"}
                              </a>
                            </td>
                            <td className="py-2 text-muted-foreground">{slot.campaign_name}</td>
                            <td className="py-2 text-right">
                              <StatusBadge status={slot.status === "sent" ? "connection_requested" : "scheduled"} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="health" className="mt-4 space-y-4">
            <LocalTimeCard
              timezone={account.timezone}
              workStartHour={account.work_start_hour}
              workEndHour={account.work_end_hour}
            />
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Cookie Status</p>
                  <div className="mt-1">
                    <StatusBadge status={account.status} />
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Last Activity</p>
                  <p className={`mt-1 text-lg font-semibold ${health?.days_since_last_activity != null && health.days_since_last_activity > 1 ? "text-amber-500" : ""}`}>
                    {health?.last_action_at
                      ? health.days_since_last_activity === 0
                        ? "Today"
                        : health.days_since_last_activity === 1
                        ? "Yesterday"
                        : `${health.days_since_last_activity}d ago`
                      : "Never"}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Error Rate (7d)</p>
                  <p className={`mt-1 text-lg font-semibold ${
                    (health?.error_rate_7d ?? 0) > 25 ? "text-red-500" :
                    (health?.error_rate_7d ?? 0) > 10 ? "text-amber-500" : ""
                  }`}>
                    {health ? `${health.error_rate_7d}%` : "—"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {health ? `${health.errors_7d} of ${health.total_actions_7d} actions` : ""}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">Proxy</p>
                  <p className="mt-1 text-lg font-semibold">
                    {account.proxy_country?.toUpperCase() || "None"}
                  </p>
                </CardContent>
              </Card>
            </div>
            {health?.last_error_message && (
              <Card className="mt-4">
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground mb-1">Last Error</p>
                  <p className="text-sm text-red-400">{health.last_error_message}</p>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ActivityTimeline logs={activity ?? []} timezone={account.timezone} />
          </TabsContent>

          <TabsContent value="settings" className="mt-4 space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Account Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 max-w-lg">
                <div>
                  <label className="text-xs text-muted-foreground">Name</label>
                  <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Timezone</label>
                  <select
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    value={editTimezone}
                    onChange={(e) => setEditTimezone(e.target.value)}
                  >
                    <option value="America/New_York">US — EST (New York)</option>
                    <option value="Europe/London">UK — GMT (London)</option>
                    <option value="Europe/Berlin">Europe — CET (Berlin)</option>
                    <option value="Europe/Rome">Italy — CET (Rome)</option>
                    <option value="Europe/Athens">Greece — EET (Athens)</option>
                    <option value="Europe/Paris">France — CET (Paris)</option>
                    <option value="Europe/Madrid">Spain — CET (Madrid)</option>
                    <option value="Asia/Dubai">UAE — GST (Dubai)</option>
                    <option value="Asia/Singapore">Asia — SGT (Singapore)</option>
                    <option value="America/Toronto">Canada — EST (Toronto)</option>
                    <option value="America/Vancouver">Canada — PST (Vancouver)</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs text-muted-foreground">Daily Limit</label>
                    <Input
                      type="number"
                      value={editDailyLimit}
                      onChange={(e) => setEditDailyLimit(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Weekly Limit</label>
                    <Input
                      type="number"
                      value={editWeeklyLimit}
                      onChange={(e) => setEditWeeklyLimit(e.target.value)}
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    Auto-Withdraw Threshold
                    {account.pending_requests != null && (
                      <span className={`ml-2 ${account.pending_requests > 1000 ? "text-amber-400" : "text-muted-foreground"}`}>
                        ({account.pending_requests} pending now)
                      </span>
                    )}
                  </label>
                  <Input
                    type="number"
                    value={editWithdrawThreshold}
                    onChange={(e) => setEditWithdrawThreshold(e.target.value)}
                    placeholder="e.g. 1500 — withdraws oldest invitations when exceeded"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    Leave empty to disable. When pending invitations exceed this number, the oldest are automatically withdrawn (10/day during acceptance check).
                  </p>
                </div>
                <ProxySettings
                  value={proxyForm}
                  onChange={setProxyForm}
                  hasStoredPassword={account.proxy_password_set}
                  passwordEditing={passwordEditing}
                  onPasswordEditingChange={setPasswordEditing}
                  accountId={id}
                />
                <p className="text-xs text-muted-foreground">
                  Created {new Date(account.created_at.endsWith("Z") ? account.created_at : account.created_at + "Z").toLocaleDateString(undefined, { timeZone: account.timezone ?? undefined })}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>LinkedIn Authentication</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5 max-w-lg">

                {/* Primary: browser login */}
                {!loginSessionActive ? (
                  <div className="space-y-2">
                    <p className="text-sm text-muted-foreground">
                      Open a browser session on the server and log in to LinkedIn normally.
                      Your cookies are saved automatically.
                    </p>
                    <Button
                      className="w-full"
                      onClick={async () => {
                        const loginWindow = window.open("about:blank", "_blank");
                        setLoginLoading(true);
                        try {
                          const res = await startLoginSession(id);
                          setLoginSessionActive(true);
                          const novncBase =
                            process.env.NEXT_PUBLIC_NOVNC_URL ||
                            `http://${window.location.hostname}:6080`;
                          const url = `${novncBase}${res.novnc_url}`;
                          if (loginWindow) {
                            loginWindow.location.href = url;
                          } else {
                            window.open(url, "_blank");
                          }
                        } catch (err: unknown) {
                          loginWindow?.close();
                          toast.error(err instanceof Error ? err.message : "Failed to start session");
                        } finally {
                          setLoginLoading(false);
                        }
                      }}
                      disabled={loginLoading}
                    >
                      {loginLoading ? "Starting..." : "Open Login Browser"}
                    </Button>
                  </div>
                ) : (
                  <div className="rounded-md border border-green-500/30 bg-green-500/10 p-4 space-y-3">
                    <p className="text-sm font-medium text-green-400">
                      Browser session active — complete the login in the new tab, then click Finish.
                    </p>
                    <div className="flex gap-2">
                      <Button
                        onClick={async () => {
                          setLoginLoading(true);
                          try {
                            const res = await finishLoginSession(id);
                            if (res.success) {
                              toast.success(res.message);
                            } else {
                              toast.error(res.message);
                            }
                          } catch (err: unknown) {
                            toast.error(err instanceof Error ? err.message : "Failed to finish session");
                          } finally {
                            setLoginSessionActive(false);
                            setLoginLoading(false);
                          }
                        }}
                        disabled={loginLoading}
                      >
                        {loginLoading ? "Saving..." : "Finish & Save Cookies"}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={async () => {
                          try { await cancelLoginSession(id); } catch { /* ignore */ }
                          setLoginSessionActive(false);
                          toast.info("Login session cancelled");
                        }}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}

                {/* Advanced: paste cookie */}
                <details className="group">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground list-none flex items-center gap-1 select-none">
                    <svg className="h-3 w-3 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                    Advanced: paste li_at cookie manually
                  </summary>
                  <div className="mt-3 space-y-2">
                    <p className="text-xs text-muted-foreground">
                      Extract your <code className="rounded bg-muted px-1 py-0.5">li_at</code> cookie from your browser&apos;s DevTools (Application → Cookies → linkedin.com).
                    </p>
                    <div className="flex gap-2">
                      <Input
                        value={newCookie}
                        onChange={(e) => setNewCookie(e.target.value)}
                        placeholder="Paste li_at cookie value"
                        type="password"
                      />
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (!newCookie) return;
                          updateCookie.mutate(
                            { li_at_cookie: newCookie },
                            {
                              onSuccess: () => {
                                toast.success("Cookie saved");
                                setNewCookie("");
                              },
                              onError: (err) => toast.error(err.message),
                            }
                          );
                        }}
                        disabled={updateCookie.isPending}
                      >
                        Save
                      </Button>
                    </div>
                  </div>
                </details>

                {/* Verify section */}
                <div className="border-t border-border pt-4 space-y-3">
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Verify connection</p>

                  {connectionChecking && (
                    <div className="flex items-center gap-3 text-sm text-muted-foreground">
                      <svg className="h-4 w-4 shrink-0 animate-spin text-blue-400" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Checking session via HTTP...
                      <div className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                        <div className="h-full w-full origin-left animate-pulse rounded-full bg-blue-500" />
                      </div>
                    </div>
                  )}

                  {!connectionChecking && connectionResult && (
                    <div className={`rounded-md border px-4 py-3 ${
                      connectionResult.valid
                        ? "border-green-500/30 bg-green-500/10"
                        : "border-red-500/30 bg-red-500/10"
                    }`}>
                      <p className={`text-sm font-medium ${connectionResult.valid ? "text-green-400" : "text-red-400"}`}>
                        {connectionResult.valid
                          ? `Session valid (${connectionResult.elapsed_ms}ms)`
                          : connectionResult.reason === "redirected_to_login"
                          ? "Session expired — log in again or paste a fresh cookie"
                          : connectionResult.reason === "proxy_unreachable"
                          ? "Proxy unreachable — LinkedIn could not be reached via your proxy"
                          : connectionResult.reason === "no_cookie"
                          ? "No cookie saved — log in first"
                          : `Failed: ${connectionResult.error || connectionResult.reason}`}
                      </p>
                      {!connectionResult.valid && connectionResult.elapsed_ms != null && (
                        <p className="text-xs text-muted-foreground mt-0.5">{connectionResult.elapsed_ms}ms</p>
                      )}
                    </div>
                  )}

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      setConnectionChecking(true);
                      setConnectionResult(null);
                      try {
                        const result = await checkConnection(id);
                        setConnectionResult(result);
                      } catch (err: unknown) {
                        setConnectionResult({ valid: false, error: err instanceof Error ? err.message : "Check failed" });
                      } finally {
                        setConnectionChecking(false);
                      }
                    }}
                    disabled={connectionChecking}
                  >
                    {connectionChecking ? "Checking..." : connectionResult ? "Check Again" : "Check Connection"}
                  </Button>
                </div>

              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
