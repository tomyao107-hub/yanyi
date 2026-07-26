import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f6f7f5",
          100: "#eceeea",
          200: "#d9ddd5",
          300: "#bbc2b6",
          400: "#929c8d",
          500: "#727d6c",
          600: "#596253",
          700: "#474e43",
          800: "#3a4038",
          900: "#30352e",
          950: "#191c18"
        },
        paper: "#f7f5ef",
        cinnabar: {
          50: "#fff5f3",
          100: "#ffe7e2",
          200: "#ffcfc4",
          300: "#ffa994",
          400: "#fa775b",
          500: "#ed5031",
          600: "#ca341d",
          700: "#a92719",
          800: "#8c241a",
          900: "#74231b",
          950: "#3f0e0a"
        }
      },
      fontFamily: {
        sans: ["Inter", "\"Noto Sans SC\"", "\"Microsoft YaHei\"", "system-ui", "sans-serif"],
        serif: ["Literata", "\"Noto Serif SC\"", "\"Songti SC\"", "Georgia", "serif"],
        mono: ["\"SFMono-Regular\"", "Consolas", "monospace"]
      },
      boxShadow: {
        card: "0 1px 2px rgba(25,28,24,.05), 0 12px 32px rgba(25,28,24,.06)",
        float: "0 16px 48px rgba(25,28,24,.14)"
      },
      keyframes: {
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" }
        },
        "soft-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: ".5" }
        }
      },
      animation: {
        "slide-up": "slide-up .2s ease-out",
        "soft-pulse": "soft-pulse 1.8s ease-in-out infinite"
      }
    }
  },
  plugins: []
} satisfies Config;
