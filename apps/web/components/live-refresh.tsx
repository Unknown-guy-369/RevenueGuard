"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

export function LiveRefresh({ intervalMs = 5_000 }: { intervalMs?: number }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const refresh = useCallback(() => {
    startTransition(() => {
      router.refresh();
      setLastRefresh(new Date());
    });
  }, [router]);

  useEffect(() => {
    const interval = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(interval);
  }, [intervalMs, refresh]);

  return (
    <button className="live-refresh" type="button" onClick={refresh} disabled={isPending}>
      <span className="pulse-dot" aria-hidden="true" />
      {isPending ? "Refreshing" : "Live"}
      {lastRefresh ? (
        <time dateTime={lastRefresh.toISOString()}>
          {lastRefresh.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </time>
      ) : (
        <span>Auto 5s</span>
      )}
    </button>
  );
}
