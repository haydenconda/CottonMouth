"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Sidebar } from "@/components/sidebar";

/**
 * Gates the whole app behind login (CORE-10694). The /login page renders
 * without the sidebar chrome; everything else redirects to /login if there's
 * no authenticated user once the initial /api/auth/me check resolves.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const isLoginPage = pathname === "/login";

  useEffect(() => {
    if (!loading && !user && !isLoginPage) {
      router.replace("/login");
    }
  }, [loading, user, isLoginPage, router]);

  if (isLoginPage) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-zinc-400">
        Loading…
      </div>
    );
  }

  if (!user) {
    // Redirect is in-flight (see effect above); render nothing to avoid a
    // flash of protected content.
    return null;
  }

  return (
    <>
      <Sidebar />
      <main className="ml-56 min-h-full">
        <div className="mx-auto max-w-7xl px-6 py-6">{children}</div>
      </main>
    </>
  );
}
