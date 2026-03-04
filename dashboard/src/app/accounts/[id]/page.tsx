"use client";

import { use, useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  useAccount,
  useAccountStats,
  useAccountActivity,
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
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { startLoginSession, finishLoginSession, cancelLoginSession, getAvatarUrl, checkConnection } from "@/lib/api";

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
  const [editProxyCountry, setEditProxyCountry] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [connectionChecking, setConnectionChecking] = useState(false);
  const [checkStep, setCheckStep] = useState(0);
  const [connectionResult, setConnectionResult] = useState<{
    valid: boolean;
    reason?: string;
    error?: string;
    elapsed_ms?: number;
  } | null>(null);
  const stepTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  if (account && !settingsInitialized) {
    setEditName(account.name);
    setEditTimezone(account.timezone ?? "");
    setEditDailyLimit(String(account.daily_limit));
    setEditWeeklyLimit(String(account.weekly_limit));
    setEditWithdrawThreshold(account.withdraw_threshold ? String(account.withdraw_threshold) : "");
    setEditProxyCountry(account.proxy_country ?? "");
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
    const newProxy = editProxyCountry.trim() || null;
    if (newProxy !== (account!.proxy_country ?? null)) data.proxy_country = newProxy;
    if (Object.keys(data).length === 0) {
      toast.info("No changes to save");
      return;
    }
    updateAccount.mutate(data as Parameters<typeof updateAccount.mutate>[0], {
      onSuccess: () => toast.success("Account settings saved"),
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
                <div>
                  <label className="text-xs text-muted-foreground">Proxy Location</label>
                  <select
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    value={editProxyCountry}
                    onChange={(e) => setEditProxyCountry(e.target.value)}
                  >
                    <option value="">No proxy</option>
                    <optgroup label="North America">
                      <option value="us">United States</option>
                      <option value="us-newyork">US — New York</option>
                      <option value="us-losangeles">US — Los Angeles</option>
                      <option value="us-chicago">US — Chicago</option>
                      <option value="us-miami">US — Miami</option>
                      <option value="us-sanfrancisco">US — San Francisco</option>
                      <option value="us-dallas">US — Dallas</option>
                      <option value="ca">Canada</option>
                      <option value="ca-toronto">Canada — Toronto</option>
                      <option value="ca-montreal">Canada — Montreal</option>
                      <option value="ca-vancouver">Canada — Vancouver</option>
                      <option value="mx">Mexico</option>
                      <option value="mx-mexicocity">Mexico — Mexico City</option>
                    </optgroup>
                    <optgroup label="Europe">
                      <option value="gb">United Kingdom</option>
                      <option value="gb-london">UK — London</option>
                      <option value="de">Germany</option>
                      <option value="de-berlin">Germany — Berlin</option>
                      <option value="de-munich">Germany — Munich</option>
                      <option value="de-frankfurt">Germany — Frankfurt</option>
                      <option value="fr">France</option>
                      <option value="fr-paris">France — Paris</option>
                      <option value="nl">Netherlands</option>
                      <option value="nl-amsterdam">Netherlands — Amsterdam</option>
                      <option value="es">Spain</option>
                      <option value="es-madrid">Spain — Madrid</option>
                      <option value="es-barcelona">Spain — Barcelona</option>
                      <option value="it">Italy</option>
                      <option value="it-rome">Italy — Rome</option>
                      <option value="it-milan">Italy — Milan</option>
                      <option value="ch">Switzerland</option>
                      <option value="ch-zurich">Switzerland — Zurich</option>
                      <option value="at">Austria</option>
                      <option value="at-vienna">Austria — Vienna</option>
                      <option value="pt">Portugal</option>
                      <option value="pt-lisbon">Portugal — Lisbon</option>
                      <option value="se">Sweden</option>
                      <option value="ie">Ireland</option>
                      <option value="pl">Poland</option>
                    </optgroup>
                    <optgroup label="Asia & Middle East">
                      <option value="sg">Singapore</option>
                      <option value="jp">Japan</option>
                      <option value="ae">UAE</option>
                      <option value="ae-dubai">UAE — Dubai</option>
                      <option value="il">Israel</option>
                      <option value="in">India</option>
                      <option value="in-mumbai">India — Mumbai</option>
                    </optgroup>
                    <optgroup label="South America">
                      <option value="br">Brazil</option>
                      <option value="br-saopaulo">Brazil — São Paulo</option>
                      <option value="ar">Argentina</option>
                      <option value="co">Colombia</option>
                    </optgroup>
                    <optgroup label="Africa">
                      <option value="za">South Africa</option>
                      <option value="ma">Morocco</option>
                      <option value="ng">Nigeria</option>
                    </optgroup>
                    <optgroup label="Oceania">
                      <option value="au">Australia</option>
                      <option value="au-sydney">Australia — Sydney</option>
                      <option value="nz">New Zealand</option>
                    </optgroup>
                  </select>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Residential proxy via IPRoyal. Each account gets a sticky IP in the selected location.
                  </p>
                </div>
                <p className="text-xs text-muted-foreground">
                  Created {new Date(account.created_at).toLocaleDateString()}
                </p>
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

                <div className="relative pt-2">
                  <div className="absolute inset-0 flex items-center">
                    <span className="w-full border-t border-border" />
                  </div>
                  <div className="relative flex justify-center text-xs uppercase">
                    <span className="bg-card px-2 text-muted-foreground">verify</span>
                  </div>
                </div>

                {/* Connection check status panel */}
                {connectionChecking && (() => {
                  const steps = [
                    "Launching browser...",
                    "Configuring proxy...",
                    "Reaching LinkedIn...",
                    "Verifying session...",
                  ];
                  return (
                    <div className="space-y-3">
                      <div className="space-y-2">
                        {steps.map((label, i) => (
                          <div key={i} className="flex items-center gap-2 text-sm">
                            {i < checkStep ? (
                              <svg className="h-4 w-4 shrink-0 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                              </svg>
                            ) : i === checkStep ? (
                              <svg className="h-4 w-4 shrink-0 animate-spin text-blue-400" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                              </svg>
                            ) : (
                              <div className="h-4 w-4 shrink-0 rounded-full border border-border" />
                            )}
                            <span className={i === checkStep ? "text-foreground" : i < checkStep ? "text-muted-foreground" : "text-muted-foreground/50"}>
                              {label}
                            </span>
                          </div>
                        ))}
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-blue-500 transition-all duration-700"
                          style={{ width: `${Math.min(((checkStep + 1) / steps.length) * 100, 95)}%` }}
                        />
                      </div>
                    </div>
                  );
                })()}

                {!connectionChecking && connectionResult && (
                  <div className={`rounded-md border px-4 py-3 ${
                    connectionResult.valid
                      ? "border-green-500/30 bg-green-500/10"
                      : "border-red-500/30 bg-red-500/10"
                  }`}>
                    <p className={`text-sm font-medium ${connectionResult.valid ? "text-green-400" : "text-red-400"}`}>
                      {connectionResult.valid
                        ? `Connection OK (${connectionResult.elapsed_ms}ms)`
                        : connectionResult.reason === "redirected_to_login"
                        ? "Session expired — cookie is invalid"
                        : connectionResult.reason === "proxy_unreachable"
                        ? "Proxy unreachable — LinkedIn could not be reached via your proxy"
                        : `Connection failed: ${connectionResult.error || connectionResult.reason}`}
                    </p>
                    {connectionResult.elapsed_ms && !connectionResult.valid && (
                      <p className="text-xs text-muted-foreground mt-1">{connectionResult.elapsed_ms}ms</p>
                    )}
                  </div>
                )}

                <Button
                  variant="outline"
                  onClick={async () => {
                    setConnectionChecking(true);
                    setConnectionResult(null);
                    setCheckStep(0);
                    // Animate steps: steps advance at roughly 0s, 3s, 7s, 12s
                    const delays = [3000, 4000, 5000];
                    let step = 0;
                    const advance = () => {
                      step += 1;
                      setCheckStep(step);
                      if (step < 3 && delays[step]) {
                        stepTimerRef.current = setTimeout(advance, delays[step]);
                      }
                    };
                    stepTimerRef.current = setTimeout(advance, delays[0]);
                    try {
                      const result = await checkConnection(id);
                      if (stepTimerRef.current) clearTimeout(stepTimerRef.current);
                      setCheckStep(4); // all done
                      setConnectionResult(result);
                    } catch (err: unknown) {
                      if (stepTimerRef.current) clearTimeout(stepTimerRef.current);
                      setConnectionResult({ valid: false, error: err instanceof Error ? err.message : "Check failed" });
                    } finally {
                      setConnectionChecking(false);
                    }
                  }}
                  disabled={connectionChecking}
                >
                  {connectionChecking ? "Checking..." : connectionResult ? "Check Again" : "Check Connection"}
                </Button>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
