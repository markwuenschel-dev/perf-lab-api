// The table fallback every chart must have — the accessible, non-color path to
// the same values (and the home for any value a chart can't directly label).
// Also the canonical styling for real data tables (e.g. the field-test log).
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface TableColumn {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  /** Right-align + tabular figures for numeric columns. */
  numeric?: boolean;
}

export interface TableViewProps {
  columns: readonly TableColumn[];
  rows: ReadonlyArray<Record<string, ReactNode>>;
  /** Accessible caption (visually hidden unless `showCaption`). */
  caption?: string;
  showCaption?: boolean;
  className?: string;
}

export function TableView({ columns, rows, caption, showCaption, className }: TableViewProps) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-[12px]">
        {caption && <caption className={cn("mb-2 text-left text-[12px] text-mute", !showCaption && "sr-only")}>{caption}</caption>}
        <thead>
          <tr className="border-b border-white/[0.08]">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "px-2 py-1.5 font-semibold uppercase tracking-[0.08em] text-faint",
                  c.numeric || c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left",
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-white/[0.04]">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn(
                    "px-2 py-1.5 text-soft",
                    c.numeric || c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left",
                  )}
                  style={c.numeric ? { fontVariantNumeric: "tabular-nums" } : undefined}
                >
                  {row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
