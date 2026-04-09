"use client";

import Link from "next/link";
import { useAccounts } from "@/hooks/use-queries";
import { getAvatarUrl } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";

const PROXY_LABELS: Record<string, string> = {
  us: "US", "us-newyork": "US / New York", "us-losangeles": "US / LA",
  "us-chicago": "US / Chicago", "us-miami": "US / Miami",
  "us-sanfrancisco": "US / SF", "us-dallas": "US / Dallas",
  ca: "Canada", "ca-toronto": "CA / Toronto", "ca-montreal": "CA / Montreal",
  "ca-vancouver": "CA / Vancouver", mx: "Mexico", "mx-mexicocity": "MX / Mexico City",
  gb: "UK", "gb-london": "UK / London",
  de: "Germany", "de-berlin": "DE / Berlin", "de-munich": "DE / Munich", "de-frankfurt": "DE / Frankfurt",
  fr: "France", "fr-paris": "FR / Paris",
  nl: "Netherlands", "nl-amsterdam": "NL / Amsterdam",
  es: "Spain", "es-madrid": "ES / Madrid", "es-barcelona": "ES / Barcelona",
  it: "Italy", "it-rome": "IT / Rome", "it-milan": "IT / Milan",
  ch: "Switzerland", "ch-zurich": "CH / Zurich",
  at: "Austria", "at-vienna": "AT / Vienna",
  pt: "Portugal", "pt-lisbon": "PT / Lisbon",
  gr: "Greece", "gr-athens": "GR / Athens",
  se: "Sweden", ie: "Ireland", pl: "Poland",
  sg: "Singapore", jp: "Japan",
  ae: "UAE", "ae-dubai": "UAE / Dubai", il: "Israel",
  "in": "India", "in-mumbai": "IN / Mumbai",
  br: "Brazil", "br-saopaulo": "BR / São Paulo", ar: "Argentina", co: "Colombia",
  za: "South Africa", ma: "Morocco", ng: "Nigeria",
  au: "Australia", "au-sydney": "AU / Sydney", nz: "New Zealand",
};

function proxyLabel(code: string | null): string {
  if (!code) return "None";
  return PROXY_LABELS[code] || code.replace("-", " / ");
}

function AccountAvatar({ accountId, name }: { accountId: string; name: string }) {
  const initials = name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <div className="relative h-10 w-10 shrink-0 overflow-hidden rounded-full bg-muted">
      <img
        src={getAvatarUrl(accountId)}
        alt=""
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
          (e.currentTarget.nextElementSibling as HTMLElement).style.display = "flex";
        }}
      />
      <span
        className="absolute inset-0 hidden items-center justify-center text-xs font-bold text-muted-foreground"
        style={{ display: "none" }}
        ref={(el) => {
          // Show initials by default until image loads
          if (el) el.style.display = "flex";
        }}
      >
        {initials}
      </span>
    </div>
  );
}

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
                    <div className="flex items-center gap-3">
                      <AccountAvatar accountId={a.id} name={a.name} />
                      <h3 className="font-medium">{a.name}</h3>
                    </div>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-xs text-muted-foreground">Daily Target</p>
                      <p>{a.daily_limit}/day</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Timezone</p>
                      <p>{a.timezone ?? "—"}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Weekly Limit</p>
                      <p>{a.weekly_limit}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Proxy</p>
                      <p>{proxyLabel(a.proxy_country)}</p>
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
