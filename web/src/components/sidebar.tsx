"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, GitBranch, Bot, ShieldCheck, Activity } from "lucide-react";
import { CottonmouthLogo } from "@/components/logo";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/events", label: "Events", icon: Activity },
  { href: "/traces", label: "Traces", icon: GitBranch },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/governance", label: "Governance", icon: ShieldCheck },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-zinc-200 bg-white">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 border-b border-zinc-200 px-5">
        <CottonmouthLogo className="h-6 w-6 text-emerald-600" />
        <span className="text-sm font-bold tracking-widest text-zinc-900">
          COTTONMOUTH
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-3 py-4">
        {navItems.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-emerald-50 text-emerald-700"
                  : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-zinc-200 px-5 py-3">
        <p className="text-[10px] uppercase tracking-wider text-zinc-400">
          Agent Observability
        </p>
      </div>
    </aside>
  );
}
