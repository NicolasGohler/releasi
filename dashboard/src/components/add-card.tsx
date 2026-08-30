import Link from "next/link";
import { Plus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Full-card "create new" affordance that occupies one grid cell alongside
 * the existing item cards on index pages (campaigns, broadcasts, accounts).
 *
 * Renders as a dashed-border card that matches the sibling cards' height
 * (via min-h) so the grid stays visually even regardless of card content
 * variance.
 */
export function AddCard({
  href,
  label,
  minHeightClass = "min-h-[172px]",
}: {
  href: string;
  label: string;
  minHeightClass?: string;
}) {
  return (
    <Link href={href}>
      <Card
        className={`h-full ${minHeightClass} border-dashed border-muted-foreground/25 hover:border-muted-foreground/50 hover:bg-muted/20 transition-colors cursor-pointer`}
      >
        <CardContent className="flex h-full flex-col items-center justify-center gap-2 p-5 text-muted-foreground/60">
          <Plus className="h-6 w-6" strokeWidth={1.5} />
          <span className="text-sm font-medium">{label}</span>
        </CardContent>
      </Card>
    </Link>
  );
}
