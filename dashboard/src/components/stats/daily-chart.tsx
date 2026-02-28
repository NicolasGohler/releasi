"use client";

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import type { DailyStat } from "@/lib/types";

interface DailyChartProps {
  data: DailyStat[];
}

export function DailyChart({ data }: DailyChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No data yet
      </div>
    );
  }

  const chartData = data.map((d) => ({
    date: d.date.slice(5), // MM-DD
    sent: d.connection_requests_sent,
    accepted: d.connections_accepted,
    errors: d.errors,
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
        <Tooltip
          contentStyle={{
            backgroundColor: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Bar dataKey="sent" fill="hsl(var(--chart-1))" radius={[3, 3, 0, 0]} name="Sent" />
        <Bar dataKey="accepted" fill="hsl(var(--chart-2))" radius={[3, 3, 0, 0]} name="Accepted" />
        <Bar dataKey="errors" fill="hsl(var(--destructive))" radius={[3, 3, 0, 0]} name="Errors" />
      </BarChart>
    </ResponsiveContainer>
  );
}
