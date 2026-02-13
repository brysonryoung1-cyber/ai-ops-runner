import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "SF Pro Display",
          "Helvetica Neue",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "SF Mono",
          "Menlo",
          "Monaco",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      colors: {
        apple: {
          bg: "#f5f5f7",
          card: "#ffffff",
          sidebar: "#f0f0f2",
          border: "#d2d2d7",
          text: "#1d1d1f",
          muted: "#6e6e73",
          blue: "#0071e3",
          green: "#34c759",
          red: "#ff3b30",
          orange: "#ff9500",
          yellow: "#ffcc00",
        },
      },
      borderRadius: {
        apple: "12px",
      },
      boxShadow: {
        apple: "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)",
        "apple-lg":
          "0 4px 12px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
