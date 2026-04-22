"use client";

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * Variables supported by the template engine (campaign/template.py).
 * `description` is shown in the tooltip so users know what actually gets
 * substituted at send time; `example` demonstrates a realistic value.
 */
const STANDARD_VARIABLES: Array<{
  token: string;
  description: string;
  example: string;
}> = [
  { token: "first_name", description: "Lead's first name (from CSV column `first_name`, `firstname`, or split from `name`)", example: "Sarah" },
  { token: "last_name",  description: "Lead's last name",  example: "Chen" },
  { token: "company",    description: "Lead's company (from CSV column `company`, `project`, or `project_name`)", example: "Acme Inc." },
  { token: "title",      description: "Lead's job title",  example: "Head of Growth" },
];

export interface MessageTemplateEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Show the 300-char LinkedIn limit counter (only for the connection note). */
  showCharLimit?: boolean;
  /** Minimum textarea height in px. */
  minHeight?: number;
  /** DOM id passed through to the textarea — useful for <label htmlFor>. */
  id?: string;
  /** Additional custom variable tokens (e.g. extra_data keys) to offer alongside the standard set. */
  customVariables?: string[];
}

export interface MessageTemplateEditorHandle {
  focus: () => void;
  insertToken: (token: string) => void;
}

/**
 * Textarea + "insert variable" chip row. Clicking a chip inserts
 * `{{token}}` at the current cursor position (or appends if unfocused),
 * then re-focuses the textarea with the cursor placed after the insert
 * so the user can keep typing naturally.
 *
 * The chip set is derived from the template engine's `_FIELD_MAP` plus
 * any `customVariables` the caller passes in. The tooltip on each chip
 * explains what the variable actually resolves to and shows an example.
 */
export const MessageTemplateEditor = forwardRef<
  MessageTemplateEditorHandle,
  MessageTemplateEditorProps
>(function MessageTemplateEditor(
  { value, onChange, placeholder, disabled, showCharLimit, minHeight = 96, id, customVariables },
  ref,
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Track cursor position even after blur so clicking a chip inserts at the
  // last caret position rather than at the end.
  const [cursor, setCursor] = useState<number | null>(null);

  function insertAtCursor(text: string) {
    const ta = textareaRef.current;
    if (!ta) {
      onChange(value + text);
      return;
    }
    // Prefer the live selection over the tracked cursor, so if the user
    // currently has the field focused with a selection the inserted text
    // replaces it.
    const activeStart = ta.selectionStart ?? cursor ?? value.length;
    const activeEnd = ta.selectionEnd ?? cursor ?? value.length;
    const before = value.slice(0, activeStart);
    const after = value.slice(activeEnd);
    const next = before + text + after;
    onChange(next);
    // Place caret after the inserted token, in the next tick so React has
    // propagated the value change.
    const nextCursor = activeStart + text.length;
    setCursor(nextCursor);
    requestAnimationFrame(() => {
      if (!textareaRef.current) return;
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(nextCursor, nextCursor);
    });
  }

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
    insertToken: (token: string) => insertAtCursor(`{{${token}}}`),
  }));

  const charCount = value.length;
  const overLimit = showCharLimit && charCount > 300;
  const approachingLimit = showCharLimit && charCount > 250 && charCount <= 300;

  const customChips = (customVariables ?? [])
    .map((t) => t.trim())
    .filter(Boolean)
    .filter((t) => !STANDARD_VARIABLES.some((v) => v.token === t));

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground mr-1">Insert:</span>
        <TooltipProvider delayDuration={150}>
          {STANDARD_VARIABLES.map((v) => (
            <Tooltip key={v.token}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 font-mono text-xs"
                  onClick={() => insertAtCursor(`{{${v.token}}}`)}
                  disabled={disabled}
                >
                  + {`{{${v.token}}}`}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                <p className="text-xs font-medium">{`{{${v.token}}}`}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{v.description}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Example: <span className="font-medium text-foreground">{v.example}</span>
                </p>
              </TooltipContent>
            </Tooltip>
          ))}
          {customChips.map((token) => (
            <Tooltip key={token}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 font-mono text-xs border-dashed"
                  onClick={() => insertAtCursor(`{{${token}}}`)}
                  disabled={disabled}
                >
                  + {`{{${token}}}`}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                <p className="text-xs font-medium">{`{{${token}}}`}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Custom variable — reads from each lead&apos;s <code className="text-[10px]">extra_data</code> JSON (set via CSV import).
                </p>
              </TooltipContent>
            </Tooltip>
          ))}
        </TooltipProvider>
      </div>
      <textarea
        id={id}
        ref={textareaRef}
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm resize-y disabled:opacity-50"
        style={{ minHeight }}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onSelect={(e) => setCursor(e.currentTarget.selectionStart)}
        onBlur={(e) => setCursor(e.currentTarget.selectionStart)}
        placeholder={placeholder}
        disabled={disabled}
      />
      {showCharLimit && (
        <div className="flex justify-between text-xs">
          <span className="text-muted-foreground">
            Missing values (e.g. blank <code>company</code>) render as empty string — no placeholder text.
          </span>
          <span
            className={
              overLimit
                ? "text-red-500 font-medium"
                : approachingLimit
                ? "text-amber-500"
                : "text-muted-foreground"
            }
          >
            {charCount} / 300 chars
            {overLimit && " — LinkedIn will reject this"}
          </span>
        </div>
      )}
    </div>
  );
});
