import { Card, CardContent } from "@/components/ui/card";

const BADGE_STYLES: Record<string, string> = {
  green: "bg-green-500/15 text-green-400 border border-green-500/30",
  amber: "bg-amber-500/15 text-amber-400 border border-amber-500/30",
  red:   "bg-red-500/15 text-red-400 border border-red-500/30",
  blue:  "bg-blue-500/15 text-blue-400 border border-blue-500/30",
};

interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  badge?: string;
  badgeColor?: "green" | "amber" | "red" | "blue";
}

export function StatCard({ label, value, sub, badge, badgeColor = "green" }: StatCardProps) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <div className="flex items-baseline gap-2 mt-1">
          <p className="text-2xl font-semibold tracking-tight">{value}</p>
          {badge && (
            <span className={`inline-block rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-none ${BADGE_STYLES[badgeColor]}`}>
              {badge}
            </span>
          )}
        </div>
        {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
      </CardContent>
    </Card>
  );
}
