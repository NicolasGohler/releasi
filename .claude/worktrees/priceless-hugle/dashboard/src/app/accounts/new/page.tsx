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

type LoginMethod = "cookie" | "browser";

export default function NewAccountPage() {
  const router = useRouter();
  const createAccount = useCreateAccount();

  const [name, setName] = useState("");
  const [cookie, setCookie] = useState("");
  const [timezone, setTimezone] = useState("Europe/Berlin");
  const [proxyCountry, setProxyCountry] = useState("");
  const [method, setMethod] = useState<LoginMethod>("cookie");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name) {
      toast.error("Account name is required");
      return;
    }
    if (method === "cookie" && !cookie) {
      toast.error("li_at cookie is required");
      return;
    }

    createAccount.mutate(
      {
        name,
        li_at_cookie: method === "cookie" ? cookie : undefined,
        timezone,
        proxy_country: proxyCountry || undefined,
      },
      {
        onSuccess: (account) => {
          if (method === "browser") {
            toast.success("Account created — use the login browser to authenticate");
          } else {
            toast.success("Account created");
          }
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
              <Label>Login Method</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={method === "cookie" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setMethod("cookie")}
                >
                  Paste Cookie
                </Button>
                <Button
                  type="button"
                  variant={method === "browser" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setMethod("browser")}
                >
                  Login via Browser
                </Button>
              </div>
            </div>

            {method === "cookie" && (
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
            )}

            {method === "browser" && (
              <div className="rounded-md border border-blue-500/30 bg-blue-500/10 px-4 py-3">
                <p className="text-sm text-blue-400">
                  After creating the account, you&apos;ll be redirected to the account page where you can open a login browser to authenticate with LinkedIn directly.
                </p>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="timezone">Timezone</Label>
              <select
                id="timezone"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
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

            <div className="space-y-2">
              <Label htmlFor="proxy">Proxy Location</Label>
              <select
                id="proxy"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={proxyCountry}
                onChange={(e) => setProxyCountry(e.target.value)}
              >
                <option value="">No proxy</option>
                <optgroup label="North America">
                  <option value="us">United States</option>
                  <option value="us-newyork">US — New York</option>
                  <option value="us-losangeles">US — Los Angeles</option>
                  <option value="us-chicago">US — Chicago</option>
                  <option value="us-miami">US — Miami</option>
                  <option value="us-sanfrancisco">US — San Francisco</option>
                  <option value="ca">Canada</option>
                  <option value="ca-toronto">Canada — Toronto</option>
                  <option value="ca-montreal">Canada — Montreal</option>
                  <option value="ca-vancouver">Canada — Vancouver</option>
                </optgroup>
                <optgroup label="Europe">
                  <option value="gb">United Kingdom</option>
                  <option value="gb-london">UK — London</option>
                  <option value="de">Germany</option>
                  <option value="de-berlin">Germany — Berlin</option>
                  <option value="de-munich">Germany — Munich</option>
                  <option value="fr">France</option>
                  <option value="fr-paris">France — Paris</option>
                  <option value="nl">Netherlands</option>
                  <option value="nl-amsterdam">Netherlands — Amsterdam</option>
                  <option value="es">Spain</option>
                  <option value="es-madrid">Spain — Madrid</option>
                  <option value="it">Italy</option>
                  <option value="ch">Switzerland</option>
                  <option value="at">Austria</option>
                  <option value="pt">Portugal</option>
                  <option value="gr">Greece</option>
                  <option value="gr-athens">Greece — Athens</option>
                  <option value="se">Sweden</option>
                  <option value="ie">Ireland</option>
                </optgroup>
                <optgroup label="Asia & Middle East">
                  <option value="sg">Singapore</option>
                  <option value="jp">Japan</option>
                  <option value="ae">UAE</option>
                  <option value="ae-dubai">UAE — Dubai</option>
                  <option value="il">Israel</option>
                  <option value="in">India</option>
                </optgroup>
                <optgroup label="South America">
                  <option value="br">Brazil</option>
                  <option value="ar">Argentina</option>
                  <option value="co">Colombia</option>
                </optgroup>
                <optgroup label="Africa & Oceania">
                  <option value="za">South Africa</option>
                  <option value="ma">Morocco</option>
                  <option value="au">Australia</option>
                  <option value="nz">New Zealand</option>
                </optgroup>
              </select>
              <p className="text-xs text-muted-foreground">
                Residential proxy via IPRoyal. Each account gets a sticky IP.
              </p>
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
