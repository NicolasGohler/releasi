"use client";

import { useState } from "react";
import Link from "next/link";
import {
  useLeadLists,
  useCreateLeadList,
  useDeleteLeadList,
  useArchiveLeadList,
  useUnarchiveLeadList,
  useScrapeStatus,
} from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EventImportDialog } from "@/components/leads/event-import-dialog";
import { ChevronDown, Archive, ArchiveRestore } from "lucide-react";
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
  const [showArchived, setShowArchived] = useState(false);
  const { data: lists, isLoading } = useLeadLists({ include_archived: showArchived });
  const deleteList = useDeleteLeadList();
  const archiveList = useArchiveLeadList();
  const unarchiveList = useUnarchiveLeadList();

  const createList = useCreateLeadList();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTgEnrich, setNewTgEnrich] = useState(true);
  const [showEventDialog, setShowEventDialog] = useState(false);
  const [scrapingIds, setScrapingIds] = useState<Set<string>>(new Set());

  const handleScrapeStarted = (listId: string) => {
    setScrapingIds((prev) => new Set(prev).add(listId));
  };

  const handleCreate = () => {
    if (!newName.trim()) return;
    createList.mutate(
      { name: newName.trim(), tg_enrich_enabled: newTgEnrich },
      {
        onSuccess: () => {
          toast.success("Lead list created");
          setNewName("");
          setNewTgEnrich(true);
          setShowCreate(false);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  };

  const activeLists = lists?.filter((ll) => !ll.archived) ?? [];
  const archivedLists = lists?.filter((ll) => ll.archived) ?? [];

  const renderCard = (ll: NonNullable<typeof lists>[0]) => {
    const isScraping = scrapingIds.has(ll.id) || ll.csv_filename === "scraping...";
    return (
      <Link key={ll.id} href={`/lead-lists/${ll.id}`}>
        <Card className={`hover:border-muted-foreground/30 transition-colors cursor-pointer ${ll.archived ? "opacity-60" : ""}`}>
          <CardContent className="p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-medium truncate">{ll.name}</h3>
              <div className="flex items-center gap-1 shrink-0">
                {ll.archived && <Badge variant="outline" className="text-xs">Archived</Badge>}
                {!ll.tg_enrich_enabled && <Badge variant="outline" className="text-xs text-muted-foreground">TG off</Badge>}
                {isScraping && <ScrapeProgressBadge listId={ll.id} />}
              </div>
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
            <div className="flex gap-2">
              {ll.archived ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => {
                    e.preventDefault();
                    unarchiveList.mutate(ll.id, {
                      onSuccess: () => toast.success("List restored"),
                      onError: (err) => toast.error(err.message),
                    });
                  }}
                >
                  <ArchiveRestore className="h-3.5 w-3.5 mr-1.5" />
                  Restore
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => {
                    e.preventDefault();
                    archiveList.mutate(ll.id, {
                      onSuccess: () => toast.success("List archived"),
                      onError: (err) => toast.error(err.message),
                    });
                  }}
                >
                  <Archive className="h-3.5 w-3.5 mr-1.5" />
                  Archive
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                className="text-destructive"
                onClick={(e) => {
                  e.preventDefault();
                  if (confirm("Delete this lead list? This cannot be undone.")) {
                    deleteList.mutate(ll.id, {
                      onSuccess: () => toast.success("List deleted"),
                      onError: (err) => toast.error(err.message),
                    });
                  }
                }}
              >
                Delete
              </Button>
            </div>
          </CardContent>
        </Card>
      </Link>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Lists" description="Manage reusable lead collections">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowArchived((v) => !v)}
          >
            <Archive className="h-4 w-4 mr-1.5" />
            {showArchived ? "Hide archived" : "Show archived"}
            {!showArchived && archivedLists.length === 0 && lists && (
              <span className="ml-1 text-muted-foreground">
                ({lists.filter((ll) => ll.archived).length})
              </span>
            )}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button>
                New List <ChevronDown className="ml-1.5 h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setShowCreate(true)}>
                Empty list
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setShowEventDialog(true)}>
                From LinkedIn Event
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </PageHeader>

      {showCreate && (
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex gap-3">
              <Input
                placeholder="List name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                autoFocus
              />
              <Button onClick={handleCreate} disabled={createList.isPending}>
                Create
              </Button>
              <Button variant="outline" onClick={() => { setShowCreate(false); setNewName(""); setNewTgEnrich(true); }}>
                Cancel
              </Button>
            </div>
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newTgEnrich}
                onChange={(e) => setNewTgEnrich(e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary"
              />
              <span>Enable Telegram enrichment</span>
              <span className="text-xs text-muted-foreground">(background sweeper will search TG handles for leads in this list)</span>
            </label>
          </CardContent>
        </Card>
      )}

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
      ) : activeLists.length === 0 && !showArchived ? (
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
        <div className="space-y-8">
          {activeLists.length > 0 && (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {activeLists.map(renderCard)}
            </div>
          )}

          {showArchived && archivedLists.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-medium text-muted-foreground">Archived</h3>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {archivedLists.map(renderCard)}
              </div>
            </div>
          )}

          {showArchived && archivedLists.length === 0 && (
            <p className="text-sm text-muted-foreground">No archived lists.</p>
          )}
        </div>
      )}
    </div>
  );
}
