"use client";

import { useMemo, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface MultiSelectOption {
  value: string;
  label: string;
}

interface MultiSelectFilterProps {
  label: string;
  options: MultiSelectOption[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  searchable?: boolean;
  className?: string;
  /** Override the trigger button text. Defaults to "All X" / "No X" / "X (n)". */
  renderTriggerLabel?: (selectedCount: number, total: number) => string;
  /** When true, an empty selection is treated as "no filter" rather than "nothing matches" for styling purposes. */
  emptyMeansAll?: boolean;
}

export function MultiSelectFilter({
  label,
  options,
  selected,
  onChange,
  searchable = false,
  className,
  renderTriggerLabel,
  emptyMeansAll = false,
}: MultiSelectFilterProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!query.trim()) return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, query]);

  function toggle(value: string) {
    const next = new Set(selected);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange(next);
  }

  function selectAll() {
    onChange(new Set(options.map((o) => o.value)));
  }

  function clearAll() {
    onChange(new Set());
  }

  const allSelected = options.length > 0 && selected.size === options.length;
  const noneSelected = selected.size === 0;
  const isFiltering = emptyMeansAll ? !allSelected && !noneSelected : !allSelected;
  const triggerLabel = renderTriggerLabel
    ? renderTriggerLabel(selected.size, options.length)
    : allSelected
    ? `All ${label.toLowerCase()}`
    : noneSelected
    ? `No ${label.toLowerCase()}`
    : `${label} (${selected.size})`;

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQuery(""); }}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "h-9 justify-between gap-2 font-normal",
            isFiltering && "border-primary/50 text-foreground",
            className
          )}
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0">
        {searchable && (
          <div className="relative border-b p-2">
            <Search className="absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              placeholder={`Search ${label.toLowerCase()}...`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-8 pl-7 text-sm"
            />
          </div>
        )}
        <div className="max-h-64 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">No matches</p>
          ) : (
            filtered.map((o) => {
              const isChecked = selected.has(o.value);
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => toggle(o.value)}
                  className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent"
                >
                  <span
                    className={cn(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border",
                      isChecked ? "border-primary bg-primary text-primary-foreground" : "border-border"
                    )}
                  >
                    {isChecked && <Check className="h-3 w-3" />}
                  </span>
                  <span className="truncate">{o.label}</span>
                </button>
              );
            })
          )}
        </div>
        <div className="flex items-center justify-between border-t p-1.5">
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" onClick={clearAll}>
            Clear
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" onClick={selectAll}>
            Select all
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
