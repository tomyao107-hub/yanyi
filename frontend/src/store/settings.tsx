import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";

export type ThemePreference = "light" | "dark" | "system";

export interface AppSettings {
  model: string;
  temperature: number;
  maxConcurrency: number;
  contextBudget: number;
  contextSegments: number;
  generateChapterSummaries: boolean;
  streamTranslation: boolean;
  exportMode: "bilingual" | "target_only";
  includeUntranslated: boolean;
  theme: ThemePreference;
  shortcutsEnabled: boolean;
}

export const defaultSettings: AppSettings = {
  model: "deepseek/deepseek-v4-flash",
  temperature: 0.3,
  maxConcurrency: 4,
  contextBudget: 1200,
  contextSegments: 3,
  generateChapterSummaries: true,
  streamTranslation: false,
  exportMode: "bilingual",
  includeUntranslated: true,
  theme: "system",
  shortcutsEnabled: true,
};

const STORAGE_KEY = "inkline:settings:v1";

function readSettings(): AppSettings {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return defaultSettings;
    return { ...defaultSettings, ...(JSON.parse(stored) as Partial<AppSettings>) };
  } catch {
    return defaultSettings;
  }
}

function setDocumentTheme(theme: ThemePreference) {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  themeMeta?.setAttribute("content", dark ? "#191c18" : "#f7f5ef");
}

interface SettingsContextValue {
  settings: AppSettings;
  providerSettingsResolved: boolean;
  updateSettings: (next: Partial<AppSettings>) => void;
  replaceSettings: (next: AppSettings) => void;
  resetSettings: () => AppSettings;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<AppSettings>(readSettings);
  const hadStoredSettings = useRef(
    window.localStorage.getItem(STORAGE_KEY) !== null,
  );
  const [providerSettingsResolved, setProviderSettingsResolved] = useState(
    hadStoredSettings.current,
  );
  const runtimeDefaults = useRef(defaultSettings);

  useEffect(() => {
    let cancelled = false;
    void api
      .runtimeSettings()
      .then((runtime) => {
        if (cancelled) return;
        const provider = runtime.provider_defaults;
        const next: AppSettings = {
          ...defaultSettings,
          model:
            typeof provider.model === "string"
              ? provider.model
              : defaultSettings.model,
          temperature:
            typeof provider.temperature === "number"
              ? provider.temperature
              : defaultSettings.temperature,
          maxConcurrency:
            typeof provider.max_concurrency === "number"
              ? provider.max_concurrency
              : defaultSettings.maxConcurrency,
          contextBudget:
            typeof provider.context_token_budget === "number"
              ? provider.context_token_budget
              : defaultSettings.contextBudget,
          contextSegments:
            typeof provider.context_segments === "number"
              ? provider.context_segments
              : defaultSettings.contextSegments,
          generateChapterSummaries:
            typeof provider.generate_chapter_summaries === "boolean"
              ? provider.generate_chapter_summaries
              : defaultSettings.generateChapterSummaries,
          streamTranslation:
            typeof provider.stream === "boolean"
              ? provider.stream
              : defaultSettings.streamTranslation,
        };
        runtimeDefaults.current = next;
        if (!hadStoredSettings.current) setSettings(next);
        setProviderSettingsResolved(true);
      })
      .catch(() => {
        // The bundled defaults keep the local UI usable while the API starts.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (providerSettingsResolved) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    }
    setDocumentTheme(settings.theme);
  }, [providerSettingsResolved, settings]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => {
      if (settings.theme === "system") setDocumentTheme("system");
    };
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [settings.theme]);

  const updateSettings = useCallback((next: Partial<AppSettings>) => {
    hadStoredSettings.current = true;
    setProviderSettingsResolved(true);
    setSettings((current) => ({ ...current, ...next }));
  }, []);
  const replaceSettings = useCallback((next: AppSettings) => {
    hadStoredSettings.current = true;
    setProviderSettingsResolved(true);
    setSettings(next);
  }, []);
  const resetSettings = useCallback(
    () => {
      hadStoredSettings.current = true;
      setProviderSettingsResolved(true);
      const next = runtimeDefaults.current;
      setSettings(next);
      return next;
    },
    [],
  );

  const value = useMemo(
    () => ({
      settings,
      providerSettingsResolved,
      updateSettings,
      replaceSettings,
      resetSettings,
    }),
    [
      settings,
      providerSettingsResolved,
      updateSettings,
      replaceSettings,
      resetSettings,
    ],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const context = useContext(SettingsContext);
  if (!context) throw new Error("useSettings must be used inside SettingsProvider");
  return context;
}

export function providerConfigFromSettings(settings: AppSettings) {
  return {
    model: settings.model,
    temperature: settings.temperature,
    max_concurrency: settings.maxConcurrency,
    context_token_budget: settings.contextBudget,
    context_segments: settings.contextSegments,
    generate_chapter_summaries: settings.generateChapterSummaries,
    stream: settings.streamTranslation,
  };
}
