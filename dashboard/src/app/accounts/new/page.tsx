"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useCreateAccount } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export default function NewAccountPage() {
  const router = useRouter();
  const createAccount = useCreateAccount();

  const [name, setName] = useState("");
  const [cookie, setCookie] = useState("");
  const [timezone, setTimezone] = useState("Europe/Berlin");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name || !cookie) {
      toast.error("Name and li_at cookie are required");
      return;
    }

    createAccount.mutate(
      { name, li_at_cookie: cookie, timezone },
      {
        onSuccess: (account) => {
          toast.success("Account created");
          router.push(`/accounts/${account.id}`);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  }

  return (
    <div className="space-y-6 max-w-xl">
      <PageHeader title="Add Account" />

      <Card>
        <CardHeader>
          <CardTitle>New LinkedIn Account</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Account Name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., nicolas"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="cookie">li_at Cookie</Label>
              <Input
                id="cookie"
                value={cookie}
                onChange={(e) => setCookie(e.target.value)}
                placeholder="Paste your li_at cookie value"
                type="password"
              />
              <p className="text-xs text-muted-foreground">
                Find this in your browser DevTools under Application &gt; Cookies &gt; linkedin.com
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="timezone">Timezone</Label>
              <select
                id="timezone"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                <option value="America/New_York">US — EST (New York)</option>
                <option value="Europe/Berlin">Europe — CET (Berlin)</option>
                <option value="Asia/Singapore">Asia — SGT (Singapore)</option>
              </select>
            </div>

            <Button type="submit" disabled={createAccount.isPending}>
              {createAccount.isPending ? "Creating..." : "Add Account"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
