"use client";

import React from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

type RevenuePoint = {
  date: string;
  collected: number;
  failed: number;
};

export function RevenueMovementChart({ data }: { data: RevenuePoint[] }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-gray-400">
        No revenue data available
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="colorCollected" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--payment-blue)" stopOpacity={0.8} />
            <stop offset="95%" stopColor="var(--payment-blue)" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="colorFailed" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--critical-red)" stopOpacity={0.35} />
            <stop offset="95%" stopColor="var(--critical-red)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="date" stroke="#cbd5e1" fontSize={12} tickLine={false} axisLine={false} />
        <YAxis
          stroke="#cbd5e1"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `₹${value / 1000}k`}
        />
        <Tooltip />
        <Area
          type="monotone"
          dataKey="collected"
          stroke="var(--payment-blue)"
          fillOpacity={1}
          fill="url(#colorCollected)"
          name="Collected"
        />
        <Area
          type="monotone"
          dataKey="failed"
          stroke="var(--critical-red)"
          fillOpacity={1}
          fill="url(#colorFailed)"
          name="Failed"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
