"use client";

import { use, useState } from "react";
import {
  useAccount,
  useAccountStats,
  useAccountActivity,
  useUpdateAccount,
  useUpdateCookie,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { StatCard } from "@/components/stats/stat-card";
import { DailyChart } from "@/components/stats/daily-chart";
import { ActivityTimeline } from "@/components/activity-timeline";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { startLoginSession, finishLoginSession, cancelLoginSession, getAvatarUrl } from "@/lib/api";

export default function AccountDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: account, isLoading } = useAccount(id);
  const { data: stats } = useAccountStats(id);
  const { data: activity } = useAccountActivity(id);
  const updateAccount = useUpdateAccount(id);
  const updateCookie = useUpdateCookie(id);
  const [newCookie, setNewCookie] = useState("");
  const [editName, setEditName] = useState("");
  const [editTimezone, setEditTimezone] = useState("");
  const [editDailyLimit, setEditDailyLimit] = useState("");
  const [editWeeklyLimit, setEditWeeklyLimit] = useState("");
  const [settingsInitialized, setSettingsInitialized] = useState(false);
  const [loginSessionActive, setLoginSessionActive] = useState(false);
  const [editWithdrawThreshold, setEditWithdrawThreshold] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);

  // Initialize edit fields from account data once loaded
  if (account && !settingsInitialized) {
    setEditName(account.name);
    setEditTimezone(account.timezone ?? "");
    setEditDailyLimit(String(account.daily_limit));
    setEditWeeklyLimit(String(account.weekly_limit));
    setEditWithdrawThreshold(account.withdraw_threshold ? String(account.withdraw_threshold) : "");
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

  // Aggregate stats
  const totalSent = stats?.reduce((a, s) => a + s.connection_requests_sent, 0) ?? 0;
  const totalAccepted = stats?.reduce((a, s) => a + s.connections_accepted, 0) ?? 0;
  const totalErrors = stats?.reduce((a, s) => a + s.errors, 0) ?? 0;
  const acceptRate = totalSent > 0 ? Math.round((totalAccepted / totalSent) * 100) : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 shrink-0 overflow-hidden rounded-full bg-muted">
          <img
            src={getAvatarUrl(id)}
            alt=""
            className="h-full w-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        </div>
        <PageHeader title={account.name}>
          <StatusBadge status={account.status} />
        </PageHeader>
      </div>

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

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Sent (30d)" value={totalSent} />
        <StatCard label="Accepted" value={totalAccepted} sub={`${acceptRate}% rate`} />
        <StatCard label="Errors" value={totalErrors} />
        <StatCard label="Daily Target" value={account.daily_limit} />
      </div>

      <Tabs defaultValue={account.status === "cookie_expired" ? "settings" : "stats"}>
        <TabsList>
          <TabsTrigger value="stats">Stats</TabsTrigger>
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

        <TabsContent value="activity" className="mt-4">
          <ActivityTimeline logs={activity ?? []} />
        </TabsContent>

        <TabsContent value="settings" className="mt-4 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Account Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 max-w-lg">
              <div>
                <label className="text-xs text-muted-foreground">Name</label>
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Timezone</label>
                <select
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={editTimezone}
                  onChange={(e) => setEditTimezone(e.target.value)}
                >
                  <option value="America/New_York">US — EST (New York)</option>
                  <option value="Europe/Berlin">Europe — CET (Berlin)</option>
                  <option value="Asia/Singapore">Asia — SGT (Singapore)</option>
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
                <label className="text-xs text-muted-foreground">Auto-Withdraw Threshold</label>
                <Input
                  type="number"
                  value={editWithdrawThreshold}
                  onChange={(e) => setEditWithdrawThreshold(e.target.value)}
                  placeholder="e.g. 1500 — withdraws oldest invitations when exceeded"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Leave empty to disable. When pending invitations exceed this number, the oldest are automatically withdrawn.
                </p>
              </div>
              <div className="flex items-center gap-3">
                <Button
                  onClick={() => {
                    const data: Record<string, unknown> = {};
                    if (editName !== account.name) data.name = editName;
                    if (editTimezone !== (account.timezone ?? "")) data.timezone = editTimezone;
                    if (Number(editDailyLimit) !== account.daily_limit) data.daily_limit = Number(editDailyLimit);
                    if (Number(editWeeklyLimit) !== account.weekly_limit) data.weekly_limit = Number(editWeeklyLimit);
                    const newThreshold = editWithdrawThreshold ? Number(editWithdrawThreshold) : null;
                    if (newThreshold !== (account.withdraw_threshold ?? null)) data.withdraw_threshold = newThreshold;
                    if (Object.keys(data).length === 0) {
                      toast.info("No changes to save");
                      return;
                    }
                    updateAccount.mutate(data as Parameters<typeof updateAccount.mutate>[0], {
                      onSuccess: () => toast.success("Account settings saved"),
                      onError: (err) => toast.error(err.message),
                    });
                  }}
                  disabled={updateAccount.isPending}
                >
                  Save Settings
                </Button>
                <span className="text-xs text-muted-foreground">
                  Created {new Date(account.created_at).toLocaleDateString()}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>LinkedIn Authentication</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 max-w-lg">
              <p className="text-sm text-muted-foreground">
                Authenticate with LinkedIn by pasting a cookie or logging in via browser.
              </p>

              <div className="space-y-2">
                <label className="text-xs text-muted-foreground">Paste li_at Cookie</label>
                <div className="flex gap-2">
                  <Input
                    value={newCookie}
                    onChange={(e) => setNewCookie(e.target.value)}
                    placeholder="Paste li_at cookie value"
                    type="password"
                  />
                  <Button
                    onClick={() => {
                      if (!newCookie) return;
                      updateCookie.mutate(
                        { li_at_cookie: newCookie },
                        {
                          onSuccess: () => {
                            toast.success("Cookie updated");
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

              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t border-border" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-card px-2 text-muted-foreground">or</span>
                </div>
              </div>

              {!loginSessionActive ? (
                <Button
                  variant="outline"
                  onClick={async () => {
                    // Open window synchronously to avoid popup blocker
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
                      toast.success("Login browser opened — complete login in the new tab");
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
              ) : (
                <div className="space-y-2">
                  <p className="text-sm font-medium text-green-600">
                    Login session active — complete the login in the browser tab
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
                      {loginLoading ? "Extracting..." : "Finish & Save Cookies"}
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={async () => {
                        try {
                          await cancelLoginSession(id);
                          toast.info("Login session cancelled");
                        } catch {
                          // ignore
                        } finally {
                          setLoginSessionActive(false);
                        }
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
