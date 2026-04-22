"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAccounts, useCreateCampaign } from "@/hooks/use-queries";
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

export default function NewCampaignPage() {
  const router = useRouter();
  const { data: accounts } = useAccounts();
  const createCampaign = useCreateCampaign();

  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [template, setTemplate] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name || !accountId) {
      toast.error("Name and account are required");
      return;
    }

    createCampaign.mutate(
      {
        account_id: accountId,
        name,
        connection_message_template: template || undefined,
      },
      {
        onSuccess: (campaign) => {
          toast.success("Campaign created");
          router.push(`/campaigns/${campaign.id}`);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  }

  return (
    <div className="space-y-6 max-w-xl">
      <PageHeader title="Create Campaign" />

      <Card>
        <CardHeader>
          <CardTitle>New Campaign</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Campaign Name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Q1 Outreach - CTOs"
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

            <div className="space-y-2">
              <Label htmlFor="template">Connection Message (optional)</Label>
              <MessageTemplateEditor
                id="template"
                value={template}
                onChange={setTemplate}
                placeholder={"Hi {{first_name}}, I'd love to connect..."}
                showCharLimit
                minHeight={110}
              />
            </div>

            <Button type="submit" disabled={createCampaign.isPending}>
              {createCampaign.isPending ? "Creating..." : "Create Campaign"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
