"use client";

import { useState } from "react";
import { useAccounts } from "@/hooks/use-queries";
import { useStartEventImport } from "@/hooks/use-queries";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";

interface EventImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted: (listId: string) => void;
}

export function EventImportDialog({ open, onOpenChange, onStarted }: EventImportDialogProps) {
  const { data: accounts } = useAccounts();
  const startImport = useStartEventImport();

  const [url, setUrl] = useState("");
  const [accountId, setAccountId] = useState("");
  const [listName, setListName] = useState("");
  const [limit, setLimit] = useState("");

  const activeAccounts = accounts?.filter((a) => !a.archived) ?? [];

  const handleSubmit = () => {
    if (!url.trim()) return toast.error("Event URL is required");
    if (!accountId) return toast.error("Select an account");

    startImport.mutate(
      {
        url: url.trim(),
        account_id: accountId,
        list_name: listName.trim() || undefined,
        limit: limit ? parseInt(limit, 10) : undefined,
      },
      {
        onSuccess: (list) => {
          toast.success("Scrape started — collecting attendees...");
          onStarted(list.id);
          onOpenChange(false);
          setUrl("");
          setListName("");
          setLimit("");
        },
        onError: (err) => toast.error(err.message),
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Import from LinkedIn Event</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="event-url">Event URL</Label>
            <Input
              id="event-url"
              placeholder="linkedin.com/search/results/people/?origin=EVENT_PAGE_CANNED_SEARCH&eventAttending=..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="account">Account</Label>
            <Select value={accountId} onValueChange={setAccountId}>
              <SelectTrigger id="account">
                <SelectValue placeholder="Select account" />
              </SelectTrigger>
              <SelectContent>
                {activeAccounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex gap-3">
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="list-name">List name</Label>
              <Input
                id="list-name"
                placeholder={`Event Attendees - ${new Date().toLocaleDateString("en-US", { month: "2-digit", day: "2-digit" })}`}
                value={listName}
                onChange={(e) => setListName(e.target.value)}
              />
            </div>
            <div className="w-28 space-y-1.5">
              <Label htmlFor="limit">Limit</Label>
              <Input
                id="limit"
                type="number"
                placeholder="All"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={startImport.isPending}>
            {startImport.isPending ? "Starting..." : "Start Scrape"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
