"use client";

import { useState } from "react";
import Link from "next/link";
import { useLeadLists, useDeleteLeadList, useScrapeStatus } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EventImportDialog } from "@/components/leads/event-import-dialog";
import { ChevronDown } from "lucide-react";
import { toast } from "sonner";

// Shows live scrape progress for a single list card
function ScrapeProgressBadge({ listId }: { listId: string }) {
  const { data: status } = useScrapeStatus(listId);
  if (!status || status.status === "unknown" || status.status === "done") return null;
  if (status.status === "error")
    return <Badge variant="destructive" className="text-xs">Scrape failed</Badge>;
  return (
    <Badge variant="secondary" className="text-xs animate-pulse">
      Scraping… {status.collected} URLs
    </Badge>
  );
}

export default function LeadListsPage() {
  const { data: lists, isLoading } = useLeadLists();
  const deleteList = useDeleteLeadList();

  const [showEventDialog, setShowEventDialog] = useState(false);
  const [scrapingIds, setScrapingIds] = useState<Set<string>>(new Set());

  const handleScrapeStarted = (listId: string) => {
    setScrapingIds((prev) => new Set(prev).add(listId));
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Lists" description="Manage reusable lead collections">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button>
              New List <ChevronDown className="ml-1.5 h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => setShowEventDialog(true)}>
              From LinkedIn Event
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </PageHeader>

      <EventImportDialog
        open={showEventDialog}
        onOpenChange={setShowEventDialog}
        onStarted={handleScrapeStarted}
      />

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : lists?.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">No lead lists yet</p>
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => setShowEventDialog(true)}
            >
              Import from LinkedIn Event
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {lists?.map((ll) => {
            const isScraping = scrapingIds.has(ll.id) || ll.csv_filename === "scraping...";
            return (
              <Link key={ll.id} href={`/lead-lists/${ll.id}`}>
                <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
                  <CardContent className="p-5 space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="font-medium truncate">{ll.name}</h3>
                      {isScraping && <ScrapeProgressBadge listId={ll.id} />}
                    </div>
                    {ll.csv_filename && ll.csv_filename !== "scraping..." && (
                      <p className="text-xs text-muted-foreground truncate">
                        {ll.csv_filename}
                      </p>
                    )}
                    <div className="flex gap-4 text-sm text-muted-foreground">
                      <span>{ll.total_leads} leads</span>
                      <span>{ll.campaign_count} campaigns</span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Created{" "}
                      {new Date(
                        ll.created_at.endsWith("Z") ? ll.created_at : ll.created_at + "Z"
                      ).toLocaleDateString()}
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-destructive"
                      onClick={(e) => {
                        e.preventDefault();
                        if (confirm("Delete this lead list?")) {
                          deleteList.mutate(ll.id, {
                            onSuccess: () => toast.success("List deleted"),
                            onError: (err) => toast.error(err.message),
                          });
                        }
                      }}
                    >
                      Delete
                    </Button>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
