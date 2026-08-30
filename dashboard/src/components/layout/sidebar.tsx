"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Target, List, Users, UserCircle, Archive, Globe, Activity, Menu, X, Mail, type LucideIcon } from "lucide-react";
import { useSidebar } from "./sidebar-context";

const navItems: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/campaigns", label: "Campaigns", icon: Target },
  { href: "/broadcasts", label: "Broadcasts", icon: Mail },
  { href: "/lead-lists", label: "Lists", icon: List },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/accounts", label: "Accounts", icon: UserCircle },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/scrapers", label: "Scrapers", icon: Globe },
  { href: "/archive", label: "Archive", icon: Archive },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isOpen, toggle, close } = useSidebar();

  return (
    <>
      {/* Mobile backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={close}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          "fixed left-0 top-0 z-40 h-screen border-r border-sidebar-border bg-sidebar transition-transform duration-200",
          // Desktop: always in flow; width switches between full and icon-only
          "md:translate-x-0",
          isOpen ? "w-56" : "md:w-14",
          // Mobile: slide in/out
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        {/* Header */}
        <div className="flex h-14 items-center border-b border-sidebar-border px-3">
          {/* Logo — hidden when desktop-collapsed */}
          <Link
            href="/campaigns"
            className={cn(
              "flex items-center gap-2.5 overflow-hidden transition-all",
              !isOpen && "md:hidden"
            )}
            onClick={() => {
              // Close drawer on mobile when navigating
              if (window.innerWidth < 768) close();
            }}
          >
            <Image
              src="/logo.png"
              alt="Releasi"
              width={28}
              height={28}
              className="shrink-0 rounded-sm"
            />
            <span className="font-brand text-xl font-medium tracking-wide text-sidebar-foreground whitespace-nowrap">
              releasi
            </span>
          </Link>

          {/* Logo icon only when desktop-collapsed */}
          <Link
            href="/campaigns"
            className={cn(
              "hidden items-center justify-center",
              !isOpen && "md:flex"
            )}
          >
            <Image
              src="/logo.png"
              alt="Releasi"
              width={28}
              height={28}
              className="rounded-sm"
            />
          </Link>

          {/* Toggle button — inside sidebar (desktop) or close button (mobile) */}
          <button
            onClick={toggle}
            className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors"
            aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {isOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>

        {/* Nav */}
        <nav className="flex flex-col gap-1 p-2">
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => {
                  if (window.innerWidth < 768) close();
                }}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  // When desktop-collapsed: center the icon
                  !isOpen && "md:justify-center md:px-2"
                )}
                title={!isOpen ? item.label : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className={cn(!isOpen && "md:hidden")}>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}

/** Hamburger button rendered in the main content area on mobile (when sidebar is closed). */
export function SidebarToggle() {
  const { isOpen, toggle } = useSidebar();
  if (isOpen) return null;
  return (
    <button
      onClick={toggle}
      className="fixed left-3 top-3 z-50 flex h-9 w-9 items-center justify-center rounded-md border border-sidebar-border bg-sidebar text-sidebar-foreground/70 shadow-sm hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors md:hidden"
      aria-label="Open sidebar"
    >
      <Menu className="h-4 w-4" />
    </button>
  );
}
