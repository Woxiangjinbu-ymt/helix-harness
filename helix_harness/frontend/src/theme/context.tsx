import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

export type Theme = "dark" | "light";

export type ChartPalette = {
  grid: string;
  axis: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  score: string;
  scoreDotStroke: string;
  reference: string;
  beforeBar: string;
  afterBar: string;
};

const DARK_CHART: ChartPalette = {
  grid: "#222b36",
  axis: "#8795a5",
  tooltipBg: "#151b23",
  tooltipBorder: "#2d3846",
  tooltipText: "#eef2f6",
  score: "#34d399",
  scoreDotStroke: "#0a1f16",
  reference: "#a78bfa",
  beforeBar: "#64748b",
  afterBar: "#34d399",
};

const LIGHT_CHART: ChartPalette = {
  grid: "#e2e8f0",
  axis: "#64748b",
  tooltipBg: "#ffffff",
  tooltipBorder: "#cbd5e1",
  tooltipText: "#0f172a",
  score: "#059669",
  scoreDotStroke: "#ffffff",
  reference: "#7c3aed",
  beforeBar: "#94a3b8",
  afterBar: "#059669",
};

type ThemeContextValue = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  chart: ChartPalette;
};

const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  setTheme: () => {},
  toggleTheme: () => {},
  chart: DARK_CHART,
});

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem("helix.theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // ignore storage failures
  }
  return "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme);

  const setTheme = (next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem("helix.theme", next);
    } catch {
      // ignore storage failures
    }
  };

  const toggleTheme = () => setTheme(theme === "dark" ? "light" : "dark");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", theme === "light" ? "#f6f7f9" : "#0a0d11");
    }
  }, [theme]);

  return (
    <ThemeContext.Provider
      value={{
        theme,
        setTheme,
        toggleTheme,
        chart: theme === "light" ? LIGHT_CHART : DARK_CHART,
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
