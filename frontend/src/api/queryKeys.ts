export const queryKeys = {
  authSession: ["auth", "session"] as const,
  runtimeSettings: ["settings", "runtime"] as const,
  providerCredentials: ["settings", "credentials"] as const,
  modelProfiles: ["settings", "model-profiles"] as const,
  promptTemplates: ["settings", "prompt-templates"] as const,
  projects: ["projects"] as const,
  project: (id: number) => ["projects", id] as const,
  segments: (id: number, chapterId?: number, status?: string) =>
    ["segments", id, chapterId ?? "all", status ?? "all"] as const,
  allSegments: (id: number) => ["segments", id] as const,
  glossary: (id: number) => ["glossary", id] as const,
  qa: (id: number) => ["qa", id] as const,
  tmStats: (id: number) => ["tm-stats", id] as const,
};
