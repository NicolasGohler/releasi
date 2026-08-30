"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  useAccounts,
  useLeadLists,
  useCreateBroadcast,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MessageTemplateEditor } from "@/components/message-template-editor";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import Link from "next/link";

export default function NewBroadcastPage() {
  const router = useRouter();
  const { data: accounts } = useAccounts();
  const { data: lists } = useLeadLists();
  const create = useCreateBroadcast();

  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [sourceListId, setSourceListId] = useState("");
  const [message1, setMessage1] = useState("");
  const [message2, setMessage2] = useState("");
  const [message3, setMessage3] = useState("");
  const [delayHours, setDelayHours] = useState(24);
  const [showMsg2, setShowMsg2] = useState(false);
  const [showMsg3, setShowMsg3] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name || !accountId || !sourceListId) {
      toast.error("Name, account, and source list are required");
      return;
    }
    if (!message1.trim()) {
      toast.error("Message 1 is required");
      return;
    }

    create.mutate(
      {
        account_id: accountId,
        name,
        source_list_id: sourceListId,
        message_1: message1,
        message_2: showMsg2 && message2 ? message2 : null,
        message_3: showMsg3 && message3 ? message3 : null,
        delay_between_hours: delayHours,
      },
      {
        onSuccess: (bc) => {
          toast.success(`Broadcast created with ${bc.total_leads} leads`);
          router.push(`/broadcasts/${bc.id}`);
        },
        onError: (err: Error) => toast.error(err.message),
      }
    );
  }

  const availableLists = (lists ?? []).filter((l) => !l.archived);
  const selectedList = availableLists.find((l) => l.id === sourceListId);

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader title="Create Broadcast" description="Message-only sequence to your 1st-degree connections">
        <Link href="/broadcasts">
          <Button variant="outline">Cancel</Button>
        </Link>
      </PageHeader>

      <Card>
        <CardHeader>
          <CardTitle>Setup</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g., Sep launch update"
                />
              </div>
              <div className="space-y-2">
                <Label>Account</Label>
                <Select value={accountId} onValueChange={setAccountId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select account" />
                  </SelectTrigger>
                  <SelectContent>
                    {accounts?.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Source List</Label>
              <Select value={sourceListId} onValueChange={setSourceListId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a lead list" />
                </SelectTrigger>
                <SelectContent>
                  {availableLists.map((l) => (
                    <SelectItem key={l.id} value={l.id}>
                      {l.name} ({l.total_leads} leads)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {selectedList && (
                <p className="text-xs text-muted-foreground">
                  {selectedList.total_leads} leads will be snapshotted at creation.
                  Only 1st-degree connections receive messages; others are marked skipped.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="m1">Message 1</Label>
              <MessageTemplateEditor
                id="m1"
                value={message1}
                onChange={setMessage1}
                placeholder={"Hey {{first_name}}, quick update..."}
                showCharLimit
                minHeight={100}
              />
            </div>

            {showMsg2 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="m2">Message 2</Label>
                  <button
                    type="button"
                    onClick={() => { setShowMsg2(false); setShowMsg3(false); setMessage2(""); setMessage3(""); }}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    remove
                  </button>
                </div>
                <MessageTemplateEditor
                  id="m2"
                  value={message2}
                  onChange={setMessage2}
                  placeholder={"Optional follow-up..."}
                  showCharLimit
                  minHeight={90}
                />
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowMsg2(true)}
              >
                + Add message 2
              </Button>
            )}

            {showMsg2 && (showMsg3 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="m3">Message 3</Label>
                  <button
                    type="button"
                    onClick={() => { setShowMsg3(false); setMessage3(""); }}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    remove
                  </button>
                </div>
                <MessageTemplateEditor
                  id="m3"
                  value={message3}
                  onChange={setMessage3}
                  placeholder={"Optional third follow-up..."}
                  showCharLimit
                  minHeight={90}
                />
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowMsg3(true)}
              >
                + Add message 3
              </Button>
            ))}

            {(showMsg2 || showMsg3) && (
              <div className="space-y-2 max-w-xs">
                <Label htmlFor="delay">Delay between messages (hours)</Label>
                <Input
                  id="delay"
                  type="number"
                  min={1}
                  max={720}
                  value={delayHours}
                  onChange={(e) => setDelayHours(parseInt(e.target.value) || 24)}
                />
              </div>
            )}

            <div className="rounded-md border border-border/50 bg-muted/30 p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground/80 mb-1">Before you activate</p>
              <p>
                Broadcast will be created as <span className="font-medium">DRAFT</span>. Review the
                snapshot on the detail page, then click <span className="font-medium">Activate</span> to
                start sending. The daily cap comes from the account&apos;s Daily message limit.
              </p>
            </div>

            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create Broadcast"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
