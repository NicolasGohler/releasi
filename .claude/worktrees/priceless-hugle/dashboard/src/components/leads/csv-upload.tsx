"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { useImportCSV } from "@/hooks/use-queries";
import { Button } from "@/components/ui/button";

interface CSVUploadProps {
  campaignId: string;
}

export function CSVUpload({ campaignId }: CSVUploadProps) {
  const importMutation = useImportCSV(campaignId);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [listName, setListName] = useState("");
  const [showDialog, setShowDialog] = useState(false);

  const doImport = useCallback(
    (file: File, name?: string) => {
      importMutation.mutate(
        { file, listName: name || undefined },
        {
          onSuccess: (data) => {
            toast.success(
              `Imported ${data.imported} leads (${data.duplicates_skipped} duplicates skipped)${name ? ` — list "${name}" created` : ""}`
            );
            setPendingFile(null);
            setListName("");
            setShowDialog(false);
          },
          onError: (err) => {
            toast.error(`Import failed: ${err.message}`);
          },
        }
      );
    },
    [importMutation]
  );

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      const file = acceptedFiles[0];
      if (!file) return;
      // Suggest a list name from the filename (without .csv extension)
      const suggested = file.name.replace(/\.csv$/i, "");
      setListName(suggested);
      setPendingFile(file);
      setShowDialog(true);
    },
    []
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"] },
    maxFiles: 1,
    disabled: importMutation.isPending,
  });

  return (
    <>
      <div
        {...getRootProps()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors ${
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-muted-foreground/50"
        }`}
      >
        <input {...getInputProps()} />
        {importMutation.isPending ? (
          <p className="text-sm text-muted-foreground">Uploading...</p>
        ) : isDragActive ? (
          <p className="text-sm text-muted-foreground">Drop CSV here</p>
        ) : (
          <div className="text-center">
            <p className="text-sm font-medium">Drop a CSV file here, or click to browse</p>
            <p className="text-xs text-muted-foreground mt-1">
              Auto-detects LinkedIn URLs and maps columns
            </p>
          </div>
        )}
      </div>

      {/* List name dialog */}
      {showDialog && pendingFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-lg">
            <h3 className="text-lg font-semibold mb-1">Save as Lead List?</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Enter a name to also save this CSV as a reusable lead list, or skip to import directly.
            </p>
            <input
              type="text"
              value={listName}
              onChange={(e) => setListName(e.target.value)}
              placeholder="Lead list name"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-4"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter" && listName.trim()) {
                  doImport(pendingFile, listName.trim());
                }
              }}
            />
            <div className="flex gap-2 justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setShowDialog(false);
                  setPendingFile(null);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => doImport(pendingFile)}
              >
                Skip
              </Button>
              <Button
                size="sm"
                disabled={!listName.trim() || importMutation.isPending}
                onClick={() => doImport(pendingFile, listName.trim())}
              >
                {importMutation.isPending ? "Importing..." : "Import & Save List"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
