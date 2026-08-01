import type {
  BatchSegmentInput,
  CostEstimate,
  ExportOptions,
  ExportResult,
  AuthSession,
  ConnectionTestResult,
  GlossaryTerm,
  LoginInput,
  ModelProfile,
  ModelProfileInput,
  Project,
  ProjectDetail,
  PromptPreview,
  PromptPreviewInput,
  PromptTemplate,
  PromptTemplateInput,
  ProviderCredential,
  ProviderCredentialInput,
  PublicSettings,
  QaIssue,
  Segment,
  SegmentIdList,
  SegmentPage,
  RuntimeLogLevel,
  RuntimeLogPage,
  TaskState,
  TranslateScope,
  TranslationMemoryStats,
  UploadProjectInput,
} from "./types";

const API_ROOT = "/api";
const CSRF_COOKIE_NAME = "trans_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function getErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (typeof record.detail === "string") return record.detail;
    if (typeof record.message === "string") return record.message;
    if (Array.isArray(record.detail)) {
      return record.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg: unknown }).msg);
          }
          return String(item);
        })
        .join("；");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken = readCookie(CSRF_COOKIE_NAME);
  if (csrfToken && UNSAFE_METHODS.has(method) && !headers.has(CSRF_HEADER_NAME)) {
    headers.set(CSRF_HEADER_NAME, csrfToken);
  }

  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new ApiError(
      getErrorMessage(payload, `请求失败（${response.status}）`),
      response.status,
      payload,
    );
  }
  return payload as T;
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) ?? null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function parseProviderConfig(value: unknown): Project["provider_cfg"] {
  if (!value) return {};
  if (typeof value === "string") {
    try {
      return JSON.parse(value) as Project["provider_cfg"];
    } catch {
      return { model: value };
    }
  }
  return asRecord(value);
}

function normalizeProject(value: unknown): Project {
  const project = asRecord(value);
  const progress = asRecord(project.progress);
  const stats = { ...asRecord(project.stats), ...progress };
  return {
    ...(project as unknown as Project),
    id: Number(project.id),
    title: String(project.title ?? "未命名书目"),
    source_lang: String(project.source_lang ?? "en"),
    target_lang: String(project.target_lang ?? "zh-CN"),
    source_type: String(project.source_type ?? "md"),
    status: String(project.status ?? "created"),
    provider_cfg: parseProviderConfig(project.provider_cfg),
    total: Number(project.total ?? project.segment_total ?? stats.total ?? 0),
    done: Number(project.done ?? project.completed ?? stats.done ?? 0),
    reviewed: Number(project.reviewed ?? stats.reviewed ?? 0),
    error: Number(project.error ?? project.errors ?? stats.error ?? 0),
    progress: Number(project.progress_percent ?? progress.percent ?? progress.percentage ?? 0),
    stats: stats as Project["stats"],
  };
}

function listFromPayload<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  const record = asRecord(payload);
  for (const key of keys) {
    if (Array.isArray(record[key])) return record[key] as T[];
  }
  return [];
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

export const api = {
  async authSession(): Promise<AuthSession> {
    try {
      return await request<AuthSession>("/auth/session");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        return { authenticated: false, admin: null };
      }
      throw error;
    }
  },

  async login(input: LoginInput): Promise<AuthSession> {
    return request<AuthSession>("/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async logout(): Promise<void> {
    await request<void>("/auth/logout", { method: "POST" });
  },

  async runtimeSettings(): Promise<PublicSettings> {
    return request<PublicSettings>("/settings");
  },

  async providerCredentials(): Promise<ProviderCredential[]> {
    return request<ProviderCredential[]>("/settings/credentials");
  },

  async createProviderCredential(
    input: ProviderCredentialInput,
  ): Promise<ProviderCredential> {
    return request<ProviderCredential>("/settings/credentials", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async rotateProviderCredential(id: number, apiKey: string): Promise<ProviderCredential> {
    return request<ProviderCredential>(`/settings/credentials/${id}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    });
  },

  async deleteProviderCredential(id: number): Promise<void> {
    await request<void>(`/settings/credentials/${id}`, { method: "DELETE" });
  },

  async modelProfiles(): Promise<ModelProfile[]> {
    return request<ModelProfile[]>("/settings/model-profiles");
  },

  async createModelProfile(input: ModelProfileInput): Promise<ModelProfile> {
    return request<ModelProfile>("/settings/model-profiles", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async updateModelProfile(
    id: number,
    input: Partial<ModelProfileInput>,
  ): Promise<ModelProfile> {
    return request<ModelProfile>(`/settings/model-profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },

  async setDefaultModelProfile(id: number): Promise<ModelProfile> {
    return request<ModelProfile>(`/settings/model-profiles/${id}/default`, {
      method: "POST",
    });
  },

  async testModelProfile(id: number): Promise<ConnectionTestResult> {
    return request<ConnectionTestResult>(`/settings/model-profiles/${id}/test`, {
      method: "POST",
    });
  },

  async deleteModelProfile(id: number): Promise<void> {
    await request<void>(`/settings/model-profiles/${id}`, { method: "DELETE" });
  },

  async promptTemplates(): Promise<PromptTemplate[]> {
    return request<PromptTemplate[]>("/settings/prompt-templates");
  },

  async createPromptTemplate(input: PromptTemplateInput): Promise<PromptTemplate> {
    return request<PromptTemplate>("/settings/prompt-templates", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async updatePromptTemplate(
    id: number,
    input: Partial<PromptTemplateInput>,
  ): Promise<PromptTemplate> {
    return request<PromptTemplate>(`/settings/prompt-templates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },

  async setDefaultPromptTemplate(id: number): Promise<PromptTemplate> {
    return request<PromptTemplate>(`/settings/prompt-templates/${id}/default`, {
      method: "POST",
    });
  },

  async deletePromptTemplate(id: number): Promise<void> {
    await request<void>(`/settings/prompt-templates/${id}`, { method: "DELETE" });
  },

  async previewPromptTemplate(input: PromptPreviewInput): Promise<PromptPreview> {
    return request<PromptPreview>("/settings/prompt-templates/preview", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async projects(): Promise<Project[]> {
    const payload = await request<unknown>("/projects");
    return listFromPayload<unknown>(payload, ["items", "projects", "data"]).map(normalizeProject);
  },

  async project(id: number): Promise<ProjectDetail> {
    const payload = await request<unknown>(`/projects/${id}`);
    const record = asRecord(payload);
    const base = normalizeProject(record.project ?? payload);
    const chapters = listFromPayload<Project>(
      record.chapters ?? asRecord(record.project).chapters,
      ["items", "chapters", "data"],
    );
    return { ...base, chapters: chapters as unknown as ProjectDetail["chapters"] };
  },

  async upload(input: UploadProjectInput): Promise<Project> {
    const form = new FormData();
    form.append("file", input.file);
    if (input.title?.trim()) form.append("title", input.title.trim());
    form.append("source_lang", input.sourceLang);
    form.append("target_lang", input.targetLang);
    if (input.providerCfg) {
      form.append("provider_cfg", JSON.stringify(input.providerCfg));
    }
    const payload = await request<unknown>("/projects", { method: "POST", body: form });
    const record = asRecord(payload);
    return normalizeProject(record.project ?? payload);
  },

  async deleteProject(id: number): Promise<void> {
    await request<void>(`/projects/${id}`, { method: "DELETE" });
  },

  async updateProject(
    id: number,
    patch: {
      title?: string;
      provider_cfg?: Project["provider_cfg"];
      model_profile_id?: number | null;
      prompt_template_id?: number | null;
    },
  ): Promise<Project> {
    return normalizeProject(
      await request<unknown>(`/projects/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    );
  },

  async startTranslation(id: number, scope?: TranslateScope): Promise<TaskState> {
    return request<TaskState>(`/projects/${id}/translate`, {
      method: "POST",
      body: JSON.stringify(scope ?? {}),
    });
  },

  async stopTranslation(id: number): Promise<TaskState> {
    return request<TaskState>(`/projects/${id}/stop`, { method: "POST" });
  },

  async estimateTranslation(id: number): Promise<CostEstimate> {
    return request<CostEstimate>(`/projects/${id}/estimate`);
  },

  async segments(
    projectId: number,
    options: { page: number; pageSize: number; chapterId?: number; status?: string },
  ): Promise<SegmentPage> {
    const payload = await request<unknown>(
      `/projects/${projectId}/segments${queryString({
        page: options.page,
        page_size: options.pageSize,
        limit: options.pageSize,
        chapter_id: options.chapterId,
        status: options.status,
      })}`,
    );
    const record = asRecord(payload);
    const items = listFromPayload<Segment>(payload, ["items", "segments", "data"]);
    const total = Number(record.total ?? record.count ?? items.length);
    const page = Number(record.page ?? options.page);
    const pageSize = Number(record.page_size ?? record.limit ?? options.pageSize);
    const hasMore =
      typeof record.has_more === "boolean"
        ? record.has_more
        : typeof record.has_next === "boolean"
          ? record.has_next
          : page * pageSize < total;
    return { items, total, page, page_size: pageSize, has_more: hasMore };
  },

  async segmentIds(
    projectId: number,
    options: { chapterId?: number; status?: string },
  ): Promise<SegmentIdList> {
    return request<SegmentIdList>(
      `/projects/${projectId}/segment-ids${queryString({
        chapter_id: options.chapterId,
        status: options.status,
      })}`,
    );
  },

  async runtimeLogs(
    projectId: number,
    options: { page?: number; pageSize?: number; level?: RuntimeLogLevel } = {},
  ): Promise<RuntimeLogPage> {
    const page = options.page ?? 1;
    const pageSize = options.pageSize ?? 200;
    const payload = await request<RuntimeLogPage>(
      `/projects/${projectId}/logs${queryString({
        page,
        page_size: pageSize,
        level: options.level,
      })}`,
    );
    return {
      ...payload,
      has_more: page * pageSize < payload.total,
    };
  },

  async updateSegment(
    id: number,
    patch: { target_text?: string; status?: string; reviewed?: boolean },
  ): Promise<Segment> {
    return request<Segment>(`/segments/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  async retranslateSegment(id: number): Promise<TaskState> {
    return request<TaskState>(`/segments/${id}/retranslate`, { method: "POST" });
  },

  async batchSegments(projectId: number, input: BatchSegmentInput): Promise<unknown> {
    return request(`/projects/${projectId}/segments/bulk`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async glossary(projectId: number): Promise<GlossaryTerm[]> {
    const payload = await request<unknown>(`/projects/${projectId}/glossary`);
    return listFromPayload<GlossaryTerm>(payload, ["items", "terms", "glossary", "data"]);
  },

  async createGlossaryTerm(
    projectId: number,
    input: Omit<GlossaryTerm, "id" | "project_id">,
  ): Promise<GlossaryTerm> {
    const result = await request<unknown>(`/projects/${projectId}/glossary`, {
      method: "POST",
      body: JSON.stringify(input),
    });
    const record = asRecord(result);
    return (Array.isArray(record.items) ? record.items[0] : result) as GlossaryTerm;
  },

  async importGlossaryCsv(
    projectId: number,
    file: File,
    overwrite = true,
  ): Promise<{ items: GlossaryTerm[]; created: number; updated: number }> {
    const form = new FormData();
    form.append("file", file);
    form.append("overwrite", String(overwrite));
    return request(`/projects/${projectId}/glossary`, {
      method: "POST",
      body: form,
    });
  },

  async updateGlossaryTerm(
    id: number,
    patch: Partial<Omit<GlossaryTerm, "id" | "project_id">>,
  ): Promise<GlossaryTerm> {
    return request<GlossaryTerm>(`/glossary/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  async deleteGlossaryTerm(id: number): Promise<void> {
    await request<void>(`/glossary/${id}`, { method: "DELETE" });
  },

  async qa(projectId: number): Promise<QaIssue[]> {
    const payload = await request<unknown>(`/projects/${projectId}/qa`);
    return listFromPayload<unknown>(payload, ["items", "issues", "data"]).map((value) => {
      const issue = asRecord(value);
      return {
        ...(issue as unknown as QaIssue),
        severity: String(issue.severity ?? issue.level ?? "warn"),
        message: String(issue.message ?? issue.detail ?? issue.rule ?? "未命名质量问题"),
        segment_id:
          issue.segment_id === null || issue.segment_id === undefined
            ? null
            : Number(issue.segment_id),
      };
    });
  },

  async tmStats(projectId: number): Promise<TranslationMemoryStats> {
    return request<TranslationMemoryStats>(`/projects/${projectId}/tm/stats`);
  },

  async createExport(projectId: number, options: ExportOptions): Promise<ExportResult> {
    return request<ExportResult>(`/projects/${projectId}/export`, {
      method: "POST",
      body: JSON.stringify(options),
    });
  },
};

export function exportUrl(result: ExportResult): string | null {
  const candidate = result.download_url;
  if (!candidate) return null;
  if (/^https?:\/\//i.test(candidate)) return candidate;
  if (candidate.startsWith("/api/")) return candidate;
  if (candidate.startsWith("api/")) return `/${candidate}`;
  return candidate.startsWith("/") ? candidate : `/api/${candidate.replace(/^\.?\//, "")}`;
}

export function projectProgress(project?: Project | null): {
  done: number;
  total: number;
  percentage: number;
} {
  const total = Number(project?.total ?? project?.stats?.total ?? 0);
  const done = Number(project?.done ?? project?.stats?.done ?? 0);
  const reviewed = Number(project?.reviewed ?? project?.stats?.reviewed ?? 0);
  const complete = Math.min(total, done + reviewed);
  const supplied = Number(project?.progress);
  const percentage = Number.isFinite(supplied) && supplied > 0
    ? Math.min(100, supplied <= 1 ? supplied * 100 : supplied)
    : total > 0
      ? (complete / total) * 100
      : 0;
  return { done: complete, total, percentage };
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "发生了未知错误，请稍后重试。";
}
