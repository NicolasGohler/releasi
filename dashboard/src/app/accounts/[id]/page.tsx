"use client";

import { use, useState } from "react";
import {
  useAccount,
  useAccountStats,
  useAccountActivity,
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

export default function AccountDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: account, isLoading } = useAccount(id);
  const { data: stats } = useAccountStats(id);
  const { data: activity } = useAccountActivity(id);
  const updateCookie = useUpdateCookie(id);
  const [newCookie, setNewCookie] = useState("");

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
      <PageHeader title={account.name}>
        <StatusBadge status={account.status} />
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Sent (30d)" value={totalSent} />
        <StatCard label="Accepted" value={totalAccepted} sub={`${acceptRate}% rate`} />
        <StatCard label="Errors" value={totalErrors} />
        <StatCard
          label="Warmup"
          value={account.warmup_enabled ? `Week ${account.warmup_week}` : "Off"}
        />
      </div>

      <Tabs defaultValue="stats">
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

        <TabsContent value="settings" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Update Cookie</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2 max-w-lg">
                <Input
                  value={newCookie}
                  onChange={(e) => setNewCookie(e.target.value)}
                  placeholder="Paste new li_at cookie value"
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
                  Update
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card className="mt-4">
            <CardHeader>
              <CardTitle>Account Info</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Timezone</p>
                <p>{account.timezone}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Daily Limit</p>
                <p>{account.daily_limit}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Weekly Limit</p>
                <p>{account.weekly_limit}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Created</p>
                <p>{new Date(account.created_at).toLocaleDateString()}</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
