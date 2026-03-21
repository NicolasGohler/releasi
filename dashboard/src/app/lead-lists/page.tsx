"use client";

import { useState } from "react";
import Link from "next/link";
import { useLeadLists, useCreateLeadList, useDeleteLeadList } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

export default function LeadListsPage() {
  const { data: lists, isLoading } = useLeadLists();
  const createList = useCreateLeadList();
  const deleteList = useDeleteLeadList();
  const [newName, setNewName] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const handleCreate = () => {
    if (!newName.trim()) return;
    createList.mutate(
      { name: newName.trim() },
      {
        onSuccess: () => {
          toast.success("Lead list created");
          setNewName("");
          setShowCreate(false);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Lists" description="Manage reusable lead collections">
        <Button onClick={() => setShowCreate(!showCreate)}>
          {showCreate ? "Cancel" : "New List"}
        </Button>
      </PageHeader>

      {showCreate && (
        <Card>
          <CardContent className="flex gap-3 p-4">
            <Input
              placeholder="List name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            <Button onClick={handleCreate} disabled={createList.isPending}>
              Create
            </Button>
          </CardContent>
        </Card>
      )}

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
              onClick={() => setShowCreate(true)}
            >
              Create your first list
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {lists?.map((ll) => (
            <Link key={ll.id} href={`/lead-lists/${ll.id}`}>
              <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
                <CardContent className="p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="font-medium truncate">{ll.name}</h3>
                  </div>
                  {ll.csv_filename && (
                    <p className="text-xs text-muted-foreground truncate">
                      {ll.csv_filename}
                    </p>
                  )}
                  <div className="flex gap-4 text-sm text-muted-foreground">
                    <span>{ll.total_leads} leads</span>
                    <span>{ll.campaign_count} campaigns</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Created {new Date(ll.created_at.endsWith("Z") ? ll.created_at : ll.created_at + "Z").toLocaleDateString()}
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
          ))}
        </div>
      )}
    </div>
  );
}
