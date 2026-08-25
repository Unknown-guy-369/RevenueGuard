import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "RevenueGuard",
    short_name: "RevenueGuard",
    description: "A bounded, verifiable revenue recovery control plane.",
    start_url: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#0052ff",
  };
}
