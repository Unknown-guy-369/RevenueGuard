import Link from "next/link";
import {
  LayoutDashboard,
  CreditCard,
  ShieldAlert,
  Activity,
  CheckSquare,
  FlaskConical,
} from "lucide-react";
import React from "react";

const navigation = [
  { name: "Home", href: "/dashboard", icon: LayoutDashboard },
  { name: "Payments", href: "/dashboard/payments", icon: CreditCard },
  { name: "Agent Recovery", href: "/dashboard/recovery", icon: ShieldAlert },
  { name: "Portfolio Incidents", href: "/dashboard/recovery/incidents", icon: Activity },
  { name: "Approvals", href: "/dashboard/approvals", icon: CheckSquare },
  { name: "Simulation Lab", href: "/dashboard/simulator", icon: FlaskConical },
];

export function DashboardShell({
  children,
  merchantId,
}: {
  children: React.ReactNode;
  merchantId: string;
}) {
  return (
    <div className="flex min-h-screen bg-canvas-grey font-sans text-ledger-navy">
      {/* Sidebar */}
      <aside className="hidden w-64 flex-col border-r border-gray-200 bg-white lg:flex">
        <div className="h-16 flex items-center px-6 border-b border-gray-200">
          <Link
            href="/dashboard"
            className="font-heading font-semibold text-lg flex items-center gap-2"
          >
            <div className="w-8 h-8 bg-ledger-navy text-white rounded-lg flex items-center justify-center font-bold">
              R
            </div>
            RevenueGuard
          </Link>
        </div>
        <nav className="flex-1 px-4 py-6 space-y-1">
          {navigation.map((item) => (
            <Link
              key={item.name}
              href={item.href}
              className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-100 hover:text-ledger-navy transition-colors"
            >
              <item.icon className="w-5 h-5 text-gray-500" />
              {item.name}
            </Link>
          ))}
        </nav>
        <div className="p-4 border-t border-gray-200">
          <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 text-payment-blue rounded-md text-xs font-mono font-semibold uppercase tracking-wider">
            <span className="w-2 h-2 rounded-full bg-payment-blue animate-pulse" />
            Test Mode
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-gray-200 bg-white/90 backdrop-blur-md">
          <div className="flex h-16 items-center justify-between gap-4 px-4 sm:px-8">
            <div className="flex items-center gap-4">
              <Link href="/dashboard" className="font-heading font-semibold lg:hidden">
                RevenueGuard
              </Link>
              <span className="hidden text-sm font-medium text-gray-600 sm:inline">
                Merchant Dashboard
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-3">
              <span className="truncate font-mono text-xs text-gray-500">{merchantId}</span>
              <form action="/api/session/sign-out" method="post">
                <button
                  className="whitespace-nowrap text-xs font-medium text-gray-600 hover:text-ledger-navy"
                  type="submit"
                >
                  Sign out
                </button>
              </form>
            </div>
          </div>
          <nav
            aria-label="Mobile dashboard navigation"
            className="flex gap-1 overflow-x-auto border-t border-gray-100 px-3 py-2 lg:hidden"
          >
            {navigation.map((item) => (
              <Link
                key={item.name}
                href={item.href}
                className="whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
              >
                {item.name}
              </Link>
            ))}
          </nav>
        </header>
        <main className="flex-1 overflow-auto p-4 sm:p-8">
          <div className="max-w-7xl mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
