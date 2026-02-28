"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/campaigns", label: "Campaigns", icon: "C" },
  { href: "/lead-lists", label: "Lead Lists", icon: "L" },
  { href: "/leads", label: "Leads", icon: "P" },
  { href: "/accounts", label: "Accounts", icon: "A" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-56 border-r border-border bg-card">
      <div className="flex h-14 items-center border-b border-border px-4">
        <Link href="/campaigns" className="text-lg font-semibold tracking-tight">
          linauto
        </Link>
      </div>
      <nav className="flex flex-col gap-1 p-3">
        {navItems.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              <span className="flex h-6 w-6 items-center justify-center rounded bg-muted text-xs font-bold">
                {item.icon}
              </span>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
