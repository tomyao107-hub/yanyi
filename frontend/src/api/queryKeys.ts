export const queryKeys = {
  projects: ["projects"] as const,
  project: (id: number) => ["projects", id] as const,
  segments: (id: number, chapterId?: number, status?: string) =>
    ["segments", id, chapterId ?? "all", status ?? "all"] as const,
  allSegments: (id: number) => ["segments", id] as const,
  glossary: (id: number) => ["glossary", id] as const,
  qa: (id: number) => ["qa", id] as const,
  tmStats: (id: number) => ["tm-stats", id] as const,
};
