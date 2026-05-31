"use client";

import { useState } from "react";
import { Globe, RefreshCw, CheckCircle2, AlertCircle, Clock } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useScrapers } from "@/hooks/use-queries";
import * as api from "@/lib/api";
import type { ScraperStatus } from "@/lib/types";

const SITE_LABELS: Record<string, string> = {
  cryptorank: "CryptoRank",
  rootdata: "RootData",
};

const SITE_DESCRIPTIONS: Record<string, string> = {
  cryptorank: "Crypto fundraising rounds from cryptorank.io",
  rootdata: "Web3 project data from rootdata.com",
};

function cookieAge(status: ScraperStatus): {
  label: string;
  color: "green" | "yellow" | "red";
} {
  if (!status.has_cookies || status.age_hours === null) {
    return { label: "No cookies yet", color: "red" };
  }
  const h = status.age_hours;
  if (h < 72) {
    const d = Math.floor(h / 24);
    const hrs = Math.round(h % 24);
    const label =
      d > 0
        ? `Fresh (${d}d ${hrs}h ago)`
        : `Fresh (${Math.round(h)}h ago)`;
    return { label, color: "green" };
  }
  if (h < 120) {
    const d = Math.floor(h / 24);
    return { label: `Stale (${d} days ago)`, color: "yellow" };
  }
  const d = Math.floor(h / 24);
  return { label: `Old (${d} days ago)`, color: "red" };
}

const colorClasses = {
  green: {
    badge: "bg-green-500/15 text-green-400 border-green-500/30",
    icon: "text-green-400",
  },
  yellow: {
    badge: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
    icon: "text-yellow-400",
  },
  red: {
    badge: "bg-red-500/15 text-red-400 border-red-500/30",
    icon: "text-red-400",
  },
};

function ScraperCard({ status }: { status: ScraperStatus }) {
  const qc = useQueryClient();
  const [sessionActive, setSessionActive] = useState(false);
  const [loading, setLoading] = useState(false);

  const site = status.site;
  const label = SITE_LABELS[site] ?? site;
  const description = SITE_DESCRIPTIONS[site] ?? "";
  const { label: ageLabel, color } = cookieAge(status);
  const Icon =
    color === "green" ? CheckCircle2 : color === "yellow" ? Clock : AlertCircle;

  const handleRefresh = async () => {
    const win = window.open("about:blank", "_blank");
    setLoading(true);
    try {
      const res = await api.startScraperLoginSession(site);
      setSessionActive(true);
      const novncBase =
        process.env.NEXT_PUBLIC_NOVNC_URL ||
        `http://${window.location.hostname}:6080`;
      const title = encodeURIComponent(`${label} Login`);
      const url = `${novncBase}${res.novnc_url}&title=${title}`;
      if (win) {
        win.location.href = url;
      } else {
        window.open(url, "_blank");
      }
    } catch (err: unknown) {
      win?.close();
      toast.error(err instanceof Error ? err.message : "Failed to start session");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      const res = await api.finishScraperLoginSession(site);
      toast.success(`Saved ${res.cookie_count} cookies for ${label}`);
      qc.invalidateQueries({ queryKey: ["scrapers"] });
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save cookies");
    } finally {
      setSessionActive(false);
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    try {
      await api.cancelScraperLoginSession(site);
    } catch {
      // ignore
    }
    setSessionActive(false);
  };

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted">
            <Globe className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-tight">{label}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
          </div>
        </div>
      </div>

      <div
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 w-fit text-xs font-medium ${colorClasses[color].badge}`}
      >
        <Icon className={`h-3.5 w-3.5 ${colorClasses[color].icon}`} />
        {ageLabel}
      </div>

      {!sessionActive ? (
        <Button
          size="sm"
          variant="outline"
          className="w-full"
          onClick={handleRefresh}
          disabled={loading}
        >
          <RefreshCw className="mr-2 h-3.5 w-3.5" />
          {loading ? "Starting..." : "Refresh Login"}
        </Button>
      ) : (
        <div className="rounded-md border border-green-500/30 bg-green-500/10 p-3 space-y-2.5">
          <p className="text-xs font-medium text-green-400">
            Browser open — log in, then save cookies.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave} disabled={loading}>
              {loading ? "Saving..." : "Save Cookies"}
            </Button>
            <Button size="sm" variant="ghost" onClick={handleCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ScrapersPage() {
  const { data: scrapers, isLoading } = useScrapers();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Scrapers</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Refresh browser cookies for the fundraising data scrapers.
        </p>
      </div>

      {isLoading ? (
        <div className="text-sm text-muted-foreground">Loading...</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(scrapers ?? []).map((s) => (
            <ScraperCard key={s.site} status={s} />
          ))}
        </div>
      )}
    </div>
  );
}
