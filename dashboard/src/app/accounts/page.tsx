"use client";

import Link from "next/link";
import { useAccounts } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";

export default function AccountsPage() {
  const { data: accounts, isLoading } = useAccounts();

  return (
    <div className="space-y-6">
      <PageHeader title="Accounts" description="Manage your LinkedIn accounts">
        <Link href="/accounts/new">
          <Button>Add Account</Button>
        </Link>
      </PageHeader>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : accounts?.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">No accounts yet</p>
            <Link href="/accounts/new">
              <Button variant="outline" className="mt-4">
                Add your first account
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {accounts?.map((a) => (
            <Link key={a.id} href={`/accounts/${a.id}`}>
              <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
                <CardContent className="p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-medium">{a.name}</h3>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-xs text-muted-foreground">Warmup</p>
                      <p>
                        {a.warmup_enabled
                          ? `Week ${a.warmup_week}`
                          : "Disabled"}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Timezone</p>
                      <p>{a.timezone ?? "—"}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Daily Limit</p>
                      <p>{a.daily_limit}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Weekly Limit</p>
                      <p>{a.weekly_limit}</p>
                    </div>
                  </div>
                  {a.paused_until && (
                    <p className="text-xs text-amber-400">
                      Paused until {new Date(a.paused_until).toLocaleString()}
                    </p>
                  )}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
