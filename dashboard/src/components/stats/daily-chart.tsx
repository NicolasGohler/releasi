"use client";

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";
import type { DailyStat } from "@/lib/types";

interface DailyChartProps {
  data: DailyStat[];
}

const COLORS = {
  sent: "#3b82f6",      // blue-500
  accepted: "#22c55e",  // green-500
  errors: "#ef4444",    // red-500
};

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
    <ResponsiveContainer width="100%" height={260}>
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
        <Legend
          wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
          formatter={(value) =>
            value === "sent" ? "Sent" : value === "accepted" ? "Accepted" : "Errors"
          }
        />
        <Bar dataKey="sent" fill={COLORS.sent} radius={[3, 3, 0, 0]} name="sent" />
        <Bar dataKey="accepted" fill={COLORS.accepted} radius={[3, 3, 0, 0]} name="accepted" />
        <Bar dataKey="errors" fill={COLORS.errors} radius={[3, 3, 0, 0]} name="errors" />
      </BarChart>
    </ResponsiveContainer>
  );
}
