"use client";

import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import { useImportCSV } from "@/hooks/use-queries";

interface CSVUploadProps {
  campaignId: string;
}

export function CSVUpload({ campaignId }: CSVUploadProps) {
  const importMutation = useImportCSV(campaignId);

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      const file = acceptedFiles[0];
      if (!file) return;

      importMutation.mutate(file, {
        onSuccess: (data) => {
          toast.success(
            `Imported ${data.imported} leads (${data.duplicates_skipped} duplicates skipped)`
          );
        },
        onError: (err) => {
          toast.error(`Import failed: ${err.message}`);
        },
      });
    },
    [importMutation]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/csv": [".csv"] },
    maxFiles: 1,
  });

  return (
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
  );
}
