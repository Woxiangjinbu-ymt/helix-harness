import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { en } from "./en";
import { zh } from "./zh";
import type { Locale, Messages } from "./types";

const DICTIONARIES: Record<Locale, Messages> = { en, zh };

type I18nContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: Messages;
};

const I18nContext = createContext<I18nContextValue>({
  locale: "en",
  setLocale: () => {},
  toggleLocale: () => {},
  t: en,
});

function readStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem("helix.locale");
    if (stored === "en" || stored === "zh") return stored;
  } catch {
    // ignore
  }
  return "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  const setLocale = (next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem("helix.locale", next);
    } catch {
      // ignore
    }
  };

  const toggleLocale = () => setLocale(locale === "en" ? "zh" : "en");

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  }, [locale]);

  return (
    <I18nContext.Provider
      value={{
        locale,
        setLocale,
        toggleLocale,
        t: DICTIONARIES[locale],
      }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
