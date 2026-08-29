import type { Config } from "tailwindcss";

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        canvas: {
          grey: "#F5F7FA",
        },
        ledger: {
          navy: "#10243E",
        },
        payment: {
          blue: "#2563EB",
        },
        verified: {
          green: "#16825D",
        },
        risk: {
          amber: "#C47A16",
        },
        critical: {
          red: "#C43D4B",
        },
      },
      fontFamily: {
        sans: ["var(--font-source-sans)", "sans-serif"],
        heading: ["var(--font-sora)", "sans-serif"],
        mono: ["var(--font-ibm-plex-mono)", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
