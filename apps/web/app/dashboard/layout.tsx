import { redirect } from "next/navigation";

import { DashboardShell } from "@/components/DashboardShell";
import { getDashboardSession } from "@/lib/auth/session";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const session = await getDashboardSession();
  if (session === null) redirect("/sign-in");
  return <DashboardShell merchantId={session.merchant_id}>{children}</DashboardShell>;
}
