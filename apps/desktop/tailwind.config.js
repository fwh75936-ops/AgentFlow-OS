/** Tailwind CSS v3 configuration.
 *
 * The theme maps 1:1 onto the AgentFlow Pure Light palette (the project's
 * own colors). Preflight is OFF: the existing hand-written global styles
 * in src/styles/globals.css stay authoritative during the migration, and
 * Tailwind is used for the vendored shadcn/ui + Kibo UI components and
 * new utility-based layout. */
import animate from "tailwindcss-animate";

/** @type {import('tailwindcss').Config} */
export default {
  corePlugins: { preflight: false },
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
    extend: {
      colors: {
        border: "#E8E8E5",
        input: "#E8E8E5",
        ring: "#3978C6",
        background: "#FFFFFF",
        foreground: "#202020",
        primary: { DEFAULT: "#242424", foreground: "#FFFFFF" },
        secondary: { DEFAULT: "#F3F3F1", foreground: "#202020" },
        destructive: { DEFAULT: "#D9534F", foreground: "#FFFFFF" },
        muted: { DEFAULT: "#F3F3F1", foreground: "#777777" },
        accent: { DEFAULT: "#EBEBE8", foreground: "#202020" },
        popover: { DEFAULT: "#FFFFFF", foreground: "#202020" },
        card: { DEFAULT: "#FFFFFF", foreground: "#202020" },
        sidebar: { DEFAULT: "#F7F7F5", foreground: "#202020", accent: "#EBEBE8" },
        hover: "#F1F1EF",
        success: { DEFAULT: "#238B61", foreground: "#FFFFFF", tint: "#E3F2EC" },
        warning: { DEFAULT: "#C58A32", foreground: "#FFFFFF", tint: "#F8F0E0" },
        info: { DEFAULT: "#3978C6", foreground: "#FFFFFF", tint: "#E7EFF9" },
        errortint: "#FBEAEA",
        /* Category palette — mirrors the --cat-* CSS variables in
           globals.css; classification color only, never decoration. */
        cat: {
          blue: { DEFAULT: "#3978C6", tint: "#E7EFF9" },
          green: { DEFAULT: "#238B61", tint: "#E3F2EC" },
          orange: { DEFAULT: "#D97B29", tint: "#FAF0E2" },
          violet: { DEFAULT: "#7C5CBF", tint: "#F0EAF9" },
          teal: { DEFAULT: "#1E8A7A", tint: "#E3F2EF" },
          rose: { DEFAULT: "#C6507B", tint: "#FAE9F1" },
          gold: { DEFAULT: "#C58A32", tint: "#F8F0E0" },
          slate: { DEFAULT: "#75756E", tint: "#EFEFEA" },
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
      },
    },
  },
  plugins: [animate],
};
