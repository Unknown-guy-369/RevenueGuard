import React from "react";

interface ProportionalBarProps {
  successPercentage: number;
  failedPercentage: number;
  recoveredPercentage: number;
}

export function ProportionalBar({
  successPercentage,
  failedPercentage,
  recoveredPercentage,
}: ProportionalBarProps) {
  // If no data, render an empty state bar
  const total = successPercentage + failedPercentage + recoveredPercentage;

  if (total === 0) {
    return <div className="w-full h-3 rounded-full bg-gray-100 overflow-hidden flex" />;
  }

  return (
    <div className="w-full h-3 rounded-full bg-gray-100 overflow-hidden flex">
      <div
        style={{ width: `${successPercentage}%` }}
        className="bg-payment-blue transition-all duration-500"
        title={`Success: ${successPercentage.toFixed(1)}%`}
      />
      <div
        style={{ width: `${recoveredPercentage}%` }}
        className="bg-verified-green transition-all duration-500"
        title={`Recovered: ${recoveredPercentage.toFixed(1)}%`}
      />
      <div
        style={{ width: `${failedPercentage}%` }}
        className="bg-critical-red transition-all duration-500"
        title={`Failed: ${failedPercentage.toFixed(1)}%`}
      />
    </div>
  );
}
