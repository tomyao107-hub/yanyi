import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  FlaskConical,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type {
  ModelProfile,
  ModelProfileInput,
  PromptTemplate,
  PromptTemplateInput,
  ProviderCredentialInput,
} from "../api/types";
import { useToast } from "../store/toast";

function ServerSection({
  icon: Icon,
  title,
  description,
  action,
  children,
}: {
  icon: typeof Server;
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="surface overflow-hidden rounded-2xl">
      <div className="flex flex-col gap-4 border-b hairline px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <h2 className="font-serif text-lg font-semibold text-ink-950 dark:text-white">{title}</h2>
            <p className="mt-0.5 text-xs leading-5 text-ink-500 dark:text-ink-400">{description}</p>
          </div>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function EmptyMessage({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-8 text-center text-sm text-ink-500 sm:px-6">
      {children}
    </div>
  );
}

function StatusPill({
  tone,
  children,
}: {
  tone: "success" | "warning" | "neutral";
  children: ReactNode;
}) {
  const classes =
    tone === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
        : "border-ink-200 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-300";
  return <span className={`badge ${classes}`}>{children}</span>;
}

const emptyCredential: ProviderCredentialInput = {
  provider: "custom",
  profile_label: "",
  api_key: "",
};

// How the profile form supplies its API key: bind an existing encrypted
// credential, encrypt a brand-new one inline, or use none (fall back to the
// backend's ambient provider environment variables).
type CredentialMode = "reuse" | "new" | "none";

interface ProfileDraft {
  displayName: string;
  provider: string;
  modelId: string;
  credentialMode: CredentialMode;
  credentialId: string;
  newCredentialLabel: string;
  newCredentialKey: string;
  baseUrl: string;
  maxConcurrency: number;
  contextWindowTokens: number;
  maxOutputTokens: number;
  generationParams: string;
  enabled: boolean;
  isDefault: boolean;
}

const emptyProfile: ProfileDraft = {
  displayName: "",
  provider: "custom",
  modelId: "",
  credentialMode: "new",
  credentialId: "",
  newCredentialLabel: "",
  newCredentialKey: "",
  baseUrl: "",
  maxConcurrency: 4,
  contextWindowTokens: 128000,
  maxOutputTokens: 4096,
  generationParams: "{}",
  enabled: true,
  isDefault: false,
};

interface TemplateDraft {
  name: string;
  description: string;
  systemPrompt: string;
  userPrefix: string;
  enabled: boolean;
  isDefault: boolean;
}

const emptyTemplate: TemplateDraft = {
  name: "",
  description: "",
  systemPrompt: "你是一名专业书籍翻译。请将{source_lang}准确、自然地翻译为{target_lang}。",
  userPrefix: "",
  enabled: true,
  isDefault: false,
};

function profileToDraft(profile: ModelProfile): ProfileDraft {
  return {
    displayName: profile.display_name,
    provider: profile.provider,
    modelId: profile.litellm_model_id,
    // An existing profile keeps whatever binding it had; a new key is only ever
    // opted into explicitly, so editing never silently rotates a shared key.
    credentialMode: profile.credential_id === null ? "none" : "reuse",
    credentialId: profile.credential_id === null ? "" : String(profile.credential_id),
    newCredentialLabel: "",
    newCredentialKey: "",
    baseUrl: profile.base_url ?? "",
    maxConcurrency: profile.max_concurrency,
    contextWindowTokens: profile.context_window_tokens,
    maxOutputTokens: profile.max_output_tokens,
    generationParams: JSON.stringify(profile.generation_params ?? {}, null, 2),
    enabled: profile.enabled,
    isDefault: profile.is_default,
  };
}

function templateToDraft(template: PromptTemplate): TemplateDraft {
  return {
    name: template.name,
    description: template.description ?? "",
    systemPrompt: template.system_prompt,
    userPrefix: template.user_prefix ?? "",
    enabled: template.enabled,
    isDefault: template.is_default,
  };
}

export function ServerSettings() {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [credentialFormOpen, setCredentialFormOpen] = useState(false);
  const [credentialDraft, setCredentialDraft] = useState(emptyCredential);
  const [rotateId, setRotateId] = useState<number | null>(null);
  const [rotateKey, setRotateKey] = useState("");
  const [profileFormOpen, setProfileFormOpen] = useState(false);
  const [profileEditId, setProfileEditId] = useState<number | null>(null);
  const [profileDraft, setProfileDraft] = useState(emptyProfile);
  const [templateFormOpen, setTemplateFormOpen] = useState(false);
  const [templateEditId, setTemplateEditId] = useState<number | null>(null);
  const [templateDraft, setTemplateDraft] = useState(emptyTemplate);
  const [previewText, setPreviewText] = useState("");

  const runtimeQuery = useQuery({
    queryKey: queryKeys.runtimeSettings,
    queryFn: api.runtimeSettings,
    staleTime: 60_000,
  });
  const credentialsQuery = useQuery({
    queryKey: queryKeys.providerCredentials,
    queryFn: api.providerCredentials,
  });
  const profilesQuery = useQuery({
    queryKey: queryKeys.modelProfiles,
    queryFn: api.modelProfiles,
  });
  const templatesQuery = useQuery({
    queryKey: queryKeys.promptTemplates,
    queryFn: api.promptTemplates,
  });

  const providers = runtimeQuery.data?.providers ?? [];
  const credentials = credentialsQuery.data ?? [];
  const profiles = profilesQuery.data ?? [];
  const templates = templatesQuery.data ?? [];
  const selectedProvider = providers.find((item) => item.name === profileDraft.provider);
  const compatibleCredentials = useMemo(
    () => credentials.filter((item) => item.provider === profileDraft.provider),
    [credentials, profileDraft.provider],
  );

  const refreshCredentials = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.providerCredentials });
  const refreshProfiles = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.modelProfiles });
  const refreshTemplates = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.promptTemplates });

  const createCredential = useMutation({
    mutationFn: api.createProviderCredential,
    onSuccess: () => {
      void refreshCredentials();
      setCredentialDraft(emptyCredential);
      setCredentialFormOpen(false);
      notify("API Key 已加密保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const rotateCredential = useMutation({
    mutationFn: ({ id, apiKey }: { id: number; apiKey: string }) =>
      api.rotateProviderCredential(id, apiKey),
    onSuccess: () => {
      void refreshCredentials();
      setRotateId(null);
      setRotateKey("");
      notify("API Key 已轮换。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const deleteCredential = useMutation({
    mutationFn: api.deleteProviderCredential,
    onSuccess: () => {
      void refreshCredentials();
      notify("凭据已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  // Created as part of saving a model, so it stays quiet: the profile save
  // reports success, and errors surface through submitProfile's try/catch.
  const createInlineCredential = useMutation({
    mutationFn: api.createProviderCredential,
    onSuccess: () => refreshCredentials(),
  });

  const saveProfile = useMutation({
    mutationFn: (input: ModelProfileInput) =>
      profileEditId === null
        ? api.createModelProfile(input)
        : api.updateModelProfile(profileEditId, input),
    onSuccess: () => {
      void refreshProfiles();
      setProfileDraft(emptyProfile);
      setProfileEditId(null);
      setProfileFormOpen(false);
      notify("模型配置已保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const setDefaultProfile = useMutation({
    mutationFn: api.setDefaultModelProfile,
    onSuccess: () => {
      void refreshProfiles();
      notify("默认模型已更新。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const testProfile = useMutation({
    mutationFn: api.testModelProfile,
    onSuccess: (result) => {
      void refreshCredentials();
      notify(
        result.ok ? `连接成功：${result.model}` : result.error_summary ?? "连接测试失败。",
        result.ok ? "success" : "error",
      );
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const deleteProfile = useMutation({
    mutationFn: api.deleteModelProfile,
    onSuccess: () => {
      void refreshProfiles();
      notify("模型配置已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const saveTemplate = useMutation({
    mutationFn: (input: PromptTemplateInput) =>
      templateEditId === null
        ? api.createPromptTemplate(input)
        : api.updatePromptTemplate(templateEditId, input),
    onSuccess: () => {
      void refreshTemplates();
      setTemplateDraft(emptyTemplate);
      setTemplateEditId(null);
      setTemplateFormOpen(false);
      setPreviewText("");
      notify("提示词模板已保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const setDefaultTemplate = useMutation({
    mutationFn: api.setDefaultPromptTemplate,
    onSuccess: () => {
      void refreshTemplates();
      notify("默认提示词已更新。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const previewTemplate = useMutation({
    mutationFn: api.previewPromptTemplate,
    onSuccess: (result) => setPreviewText(result.rendered),
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const deleteTemplate = useMutation({
    mutationFn: api.deletePromptTemplate,
    onSuccess: () => {
      void refreshTemplates();
      notify("提示词模板已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const openNewProfile = () => {
    setProfileDraft({
      ...emptyProfile,
      modelId: runtimeQuery.data?.provider_defaults.model?.toString() ?? "",
    });
    setProfileEditId(null);
    setProfileFormOpen(true);
  };

  const submitProfile = async (event: FormEvent) => {
    event.preventDefault();
    let generationParams: Record<string, unknown>;
    try {
      const parsed = JSON.parse(profileDraft.generationParams || "{}") as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("生成参数必须是 JSON 对象。");
      }
      generationParams = parsed as Record<string, unknown>;
    } catch (error) {
      notify(error instanceof Error ? error.message : "生成参数不是有效 JSON。", "error");
      return;
    }

    // Resolve the credential binding from the chosen mode. A new key is
    // encrypted first so the profile can bind its id; if that write fails the
    // profile is never created, leaving no dangling reference.
    let credentialId: number | null;
    if (profileDraft.credentialMode === "reuse") {
      if (!profileDraft.credentialId) {
        notify("请选择要复用的已保存密钥。", "error");
        return;
      }
      credentialId = Number(profileDraft.credentialId);
    } else if (profileDraft.credentialMode === "new") {
      const apiKey = profileDraft.newCredentialKey.trim();
      if (!apiKey) {
        notify("请输入新的 API Key，或改用其他密钥来源。", "error");
        return;
      }
      const label =
        profileDraft.newCredentialLabel.trim() || profileDraft.displayName.trim();
      try {
        const created = await createInlineCredential.mutateAsync({
          provider: profileDraft.provider,
          profile_label: label,
          api_key: apiKey,
        });
        credentialId = created.id;
      } catch (error) {
        notify(errorMessage(error), "error");
        return;
      }
    } else {
      credentialId = null;
    }

    saveProfile.mutate({
      display_name: profileDraft.displayName.trim(),
      provider: profileDraft.provider,
      litellm_model_id: profileDraft.modelId.trim(),
      credential_id: credentialId,
      base_url: profileDraft.baseUrl.trim() || null,
      enabled: profileDraft.enabled,
      is_default: profileDraft.isDefault,
      max_concurrency: profileDraft.maxConcurrency,
      context_window_tokens: profileDraft.contextWindowTokens,
      max_output_tokens: profileDraft.maxOutputTokens,
      generation_params: generationParams,
    });
  };

  const submitTemplate = (event: FormEvent) => {
    event.preventDefault();
    saveTemplate.mutate({
      name: templateDraft.name.trim(),
      description: templateDraft.description.trim() || null,
      system_prompt: templateDraft.systemPrompt.trim(),
      user_prefix: templateDraft.userPrefix.trim() || null,
      enabled: templateDraft.enabled,
      is_default: templateDraft.isDefault,
    });
  };

  return (
    <div className="space-y-5">
      {runtimeQuery.data?.credential_key_is_ephemeral && (
        <div className="flex items-start gap-3 border-y border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200 sm:rounded-xl sm:border">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>当前使用临时主密钥加密凭据。重启后可能无法解密，请在服务器配置持久化主密钥。</p>
        </div>
      )}

      <ServerSection
        icon={Server}
        title="模型配置"
        description="集中管理翻译模型：为每个模型选择供应商、模型 ID、API 地址与密钥。同一密钥可被多个模型复用。"
        action={
          <button type="button" className="btn-secondary shrink-0" onClick={openNewProfile}>
            <Plus className="size-4" />
            添加模型
          </button>
        }
      >
        {profileFormOpen && (
          <form onSubmit={submitProfile} className="border-b hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:px-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="field-label" htmlFor="profile-name">配置名称</label>
                <input
                  id="profile-name"
                  className="field"
                  value={profileDraft.displayName}
                  onChange={(event) =>
                    setProfileDraft((current) => ({ ...current, displayName: event.target.value }))
                  }
                  placeholder="例如：Gemini 国内接口"
                  required
                />
              </div>
              <div>
                <label className="field-label" htmlFor="profile-provider">供应商类型</label>
                <select
                  id="profile-provider"
                  className="field"
                  value={profileDraft.provider}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      provider: event.target.value,
                      credentialId: "",
                    }))
                  }
                >
                  {providers.map((provider) => (
                    <option key={provider.name} value={provider.name}>{provider.label}</option>
                  ))}
                </select>
                {selectedProvider?.hint && (
                  <p className="mt-1.5 text-xs leading-5 text-ink-500">{selectedProvider.hint}</p>
                )}
              </div>
              <div>
                <label className="field-label" htmlFor="profile-model">模型 ID</label>
                <input
                  id="profile-model"
                  className="field font-mono"
                  list="server-model-suggestions"
                  value={profileDraft.modelId}
                  onChange={(event) =>
                    setProfileDraft((current) => ({ ...current, modelId: event.target.value }))
                  }
                  placeholder="openai/gemini-3.1-pro-low"
                  required
                />
                <datalist id="server-model-suggestions">
                  {(runtimeQuery.data?.suggested_models ?? []).map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </div>
              <div className="sm:col-span-2">
                <span className="field-label">API 密钥</span>
                <div className="grid grid-cols-3 gap-2">
                  {([
                    ["new", "输入新密钥"],
                    ["reuse", "复用已保存"],
                    ["none", "不使用密钥"],
                  ] as const).map(([mode, label]) => (
                    <button
                      key={mode}
                      type="button"
                      className={`min-h-10 rounded-lg border px-3 text-sm font-medium transition ${
                        profileDraft.credentialMode === mode
                          ? "border-cinnabar-600 bg-cinnabar-50 text-cinnabar-800 dark:bg-cinnabar-950/40 dark:text-cinnabar-300"
                          : "border-ink-200 bg-white text-ink-600 hover:bg-ink-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-300"
                      }`}
                      aria-pressed={profileDraft.credentialMode === mode}
                      onClick={() =>
                        setProfileDraft((current) => ({ ...current, credentialMode: mode }))
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {profileDraft.credentialMode === "new" && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div>
                      <label className="field-label" htmlFor="profile-new-key">API Key</label>
                      <input
                        id="profile-new-key"
                        type="password"
                        autoComplete="new-password"
                        className="field font-mono"
                        value={profileDraft.newCredentialKey}
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            newCredentialKey: event.target.value,
                          }))
                        }
                        placeholder="输入后将立即加密，之后不可查看明文"
                      />
                    </div>
                    <div>
                      <label className="field-label" htmlFor="profile-new-label">密钥名称（可选）</label>
                      <input
                        id="profile-new-label"
                        className="field"
                        value={profileDraft.newCredentialLabel}
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            newCredentialLabel: event.target.value,
                          }))
                        }
                        placeholder="留空则使用模型名称"
                      />
                    </div>
                  </div>
                )}
                {profileDraft.credentialMode === "reuse" && (
                  <div className="mt-3">
                    {compatibleCredentials.length === 0 ? (
                      <p className="flex items-start gap-1.5 rounded-lg border hairline bg-ink-50/60 px-3 py-2.5 text-xs leading-5 text-ink-500 dark:bg-ink-950/30">
                        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                        该供应商还没有已保存的密钥。改用“输入新密钥”，或在下方“管理已保存的密钥”中添加。
                      </p>
                    ) : (
                      <select
                        className="field"
                        aria-label="选择已保存的 API 密钥"
                        value={profileDraft.credentialId}
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            credentialId: event.target.value,
                          }))
                        }
                      >
                        <option value="">选择已保存的密钥…</option>
                        {compatibleCredentials.map((credential) => (
                          <option key={credential.id} value={credential.id}>
                            {credential.profile_label}（{credential.masked_key}）
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
                {profileDraft.credentialMode === "none" && (
                  <p className="mt-3 text-xs leading-5 text-ink-500">
                    不绑定密钥，改由后端环境变量提供供应商密钥（适用于本地模型或已在服务器配置的密钥）。
                  </p>
                )}
              </div>
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="profile-url">
                  API 地址{selectedProvider?.requires_base_url ? "（必填）" : "（可选）"}
                </label>
                <input
                  id="profile-url"
                  type="url"
                  className="field font-mono"
                  value={profileDraft.baseUrl}
                  onChange={(event) =>
                    setProfileDraft((current) => ({ ...current, baseUrl: event.target.value }))
                  }
                  placeholder="https://example.com/v1"
                  required={selectedProvider?.requires_base_url}
                />
                {profileDraft.baseUrl.startsWith("http://") &&
                  !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::|\/|$)/i.test(profileDraft.baseUrl) && (
                    <p className="mt-1.5 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                      <AlertTriangle className="size-3.5" />
                      远程 HTTP 会以明文传输 API Key，建议使用 HTTPS。
                    </p>
                  )}
              </div>
              <div>
                <label className="field-label" htmlFor="profile-concurrency">最大并发</label>
                <input
                  id="profile-concurrency"
                  type="number"
                  min={1}
                  max={32}
                  className="field"
                  value={profileDraft.maxConcurrency}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      maxConcurrency: Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div>
                <label className="field-label" htmlFor="profile-output">最大输出 Token</label>
                <input
                  id="profile-output"
                  type="number"
                  min={1}
                  max={10_000_000}
                  className="field"
                  value={profileDraft.maxOutputTokens}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      maxOutputTokens: Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div>
                <label className="field-label" htmlFor="profile-context">上下文窗口 Token</label>
                <input
                  id="profile-context"
                  type="number"
                  min={1}
                  max={10_000_000}
                  className="field"
                  value={profileDraft.contextWindowTokens}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      contextWindowTokens: Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div>
                <label className="field-label" htmlFor="profile-params">生成参数（JSON）</label>
                <textarea
                  id="profile-params"
                  className="field min-h-24 resize-y font-mono"
                  value={profileDraft.generationParams}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      generationParams: event.target.value,
                    }))
                  }
                  spellCheck={false}
                />
              </div>
            </div>
            <div className="mt-4 flex flex-col gap-3 border-t hairline pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                  <input
                    type="checkbox"
                    className="size-4 accent-cinnabar-700"
                    checked={profileDraft.enabled}
                    onChange={(event) =>
                      setProfileDraft((current) => ({ ...current, enabled: event.target.checked }))
                    }
                  />
                  启用
                </label>
                <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                  <input
                    type="checkbox"
                    className="size-4 accent-cinnabar-700"
                    checked={profileDraft.isDefault}
                    onChange={(event) =>
                      setProfileDraft((current) => ({ ...current, isDefault: event.target.checked }))
                    }
                  />
                  设为默认
                </label>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setProfileFormOpen(false);
                    setProfileEditId(null);
                  }}
                >
                  取消
                </button>
                <button type="submit" className="btn-primary" disabled={saveProfile.isPending}>
                  <Save className="size-4" />
                  {profileEditId === null ? "创建配置" : "保存修改"}
                </button>
              </div>
            </div>
          </form>
        )}
        {profilesQuery.isLoading ? (
          <EmptyMessage>正在读取模型配置...</EmptyMessage>
        ) : profiles.length === 0 ? (
          <EmptyMessage>尚未创建服务端模型配置。上方的本地默认值不会保存 API Key。</EmptyMessage>
        ) : (
          <div className="divide-y hairline">
            {profiles.map((profile) => {
              const credential = credentials.find((item) => item.id === profile.credential_id);
              return (
                <div key={profile.id} className="px-5 py-4 sm:px-6">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-ink-900 dark:text-white">
                          {profile.display_name}
                        </span>
                        {profile.is_default && <StatusPill tone="success"><Star className="size-3" />默认</StatusPill>}
                        {!profile.enabled && <StatusPill tone="warning">已停用</StatusPill>}
                        {profile.insecure_transport && <StatusPill tone="warning">HTTP 明文</StatusPill>}
                      </div>
                      <p className="mt-1 break-all font-mono text-xs text-ink-600 dark:text-ink-300">
                        {profile.litellm_model_id}
                      </p>
                      <p className="mt-1 break-all text-xs text-ink-500">
                        {profile.base_url || "供应商默认 API 地址"}
                        {credential ? ` · ${credential.profile_label} ${credential.masked_key}` : " · 未绑定凭据"}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-1">
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={testProfile.isPending}
                        onClick={() => testProfile.mutate(profile.id)}
                      >
                        <FlaskConical className="size-4" />
                        测试
                      </button>
                      {!profile.is_default && (
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => setDefaultProfile.mutate(profile.id)}
                        >
                          <Star className="size-4" />
                          设为默认
                        </button>
                      )}
                      <button
                        type="button"
                        className="icon-btn"
                        title="编辑模型配置"
                        aria-label={`编辑 ${profile.display_name}`}
                        onClick={() => {
                          setProfileDraft(profileToDraft(profile));
                          setProfileEditId(profile.id);
                          setProfileFormOpen(true);
                        }}
                      >
                        <Pencil className="size-4" />
                      </button>
                      <button
                        type="button"
                        className="icon-btn text-cinnabar-600"
                        title="删除模型配置"
                        aria-label={`删除 ${profile.display_name}`}
                        disabled={profile.is_default}
                        onClick={() => {
                          if (window.confirm(`确定删除模型配置“${profile.display_name}”吗？`)) {
                            deleteProfile.mutate(profile.id);
                          }
                        }}
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {runtimeQuery.data?.connection_test_notice && (
          <p className="border-t hairline px-5 py-3 text-xs leading-5 text-ink-500 sm:px-6">
            {runtimeQuery.data.connection_test_notice}
          </p>
        )}

        <details className="group border-t hairline">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-5 py-3.5 text-sm font-medium text-ink-700 hover:bg-ink-50/60 dark:text-ink-200 dark:hover:bg-ink-950/30 sm:px-6">
            <span className="flex items-center gap-2">
              <KeyRound className="size-4 text-ink-500" />
              管理已保存的密钥
              <span className="rounded-md bg-ink-100 px-1.5 py-0.5 font-mono text-xs tabular-nums text-ink-600 dark:bg-ink-800 dark:text-ink-300">
                {credentials.length}
              </span>
            </span>
            <ChevronDown className="size-4 text-ink-500 transition-transform group-open:rotate-180" />
          </summary>
          <div className="border-t hairline">
            <div className="flex items-center justify-between gap-3 px-5 py-3 sm:px-6">
              <p className="text-xs leading-5 text-ink-500">
                密钥在服务端加密保存，读取接口和浏览器都不会得到明文。可被多个模型复用，删除前需先解除引用。
              </p>
              <button
                type="button"
                className="btn-secondary shrink-0"
                onClick={() => setCredentialFormOpen((value) => !value)}
              >
                {credentialFormOpen ? <ChevronUp className="size-4" /> : <Plus className="size-4" />}
                {credentialFormOpen ? "收起" : "添加密钥"}
              </button>
            </div>
            {credentialFormOpen && (
              <form
                className="grid gap-4 border-t hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:grid-cols-2 sm:px-6"
                onSubmit={(event) => {
                  event.preventDefault();
                  createCredential.mutate({
                    ...credentialDraft,
                    profile_label: credentialDraft.profile_label.trim(),
                    api_key: credentialDraft.api_key.trim(),
                  });
                }}
              >
                <div>
                  <label className="field-label" htmlFor="credential-provider">供应商</label>
                  <select
                    id="credential-provider"
                    className="field"
                    value={credentialDraft.provider}
                    onChange={(event) =>
                      setCredentialDraft((current) => ({ ...current, provider: event.target.value }))
                    }
                  >
                    {providers.map((provider) => (
                      <option key={provider.name} value={provider.name}>{provider.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="field-label" htmlFor="credential-label">密钥名称</label>
                  <input
                    id="credential-label"
                    className="field"
                    value={credentialDraft.profile_label}
                    onChange={(event) =>
                      setCredentialDraft((current) => ({
                        ...current,
                        profile_label: event.target.value,
                      }))
                    }
                    placeholder="例如：国内中转"
                    required
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="field-label" htmlFor="credential-key">API Key</label>
                  <input
                    id="credential-key"
                    type="password"
                    autoComplete="new-password"
                    className="field font-mono"
                    value={credentialDraft.api_key}
                    onChange={(event) =>
                      setCredentialDraft((current) => ({ ...current, api_key: event.target.value }))
                    }
                    placeholder="输入后将立即加密，之后不可查看明文"
                    required
                  />
                </div>
                <div className="flex justify-end gap-2 sm:col-span-2">
                  <button type="button" className="btn-ghost" onClick={() => setCredentialFormOpen(false)}>
                    取消
                  </button>
                  <button type="submit" className="btn-primary" disabled={createCredential.isPending}>
                    <ShieldCheck className="size-4" />
                    加密保存
                  </button>
                </div>
              </form>
            )}
            {credentialsQuery.isLoading ? (
              <EmptyMessage>正在读取密钥...</EmptyMessage>
            ) : credentials.length === 0 ? (
              <EmptyMessage>尚未单独保存密钥。添加模型时选择“输入新密钥”也会在这里保存。</EmptyMessage>
            ) : (
              <div className="divide-y hairline border-t hairline">
                {credentials.map((credential) => {
                  const usedByCount = profiles.filter(
                    (profile) => profile.credential_id === credential.id,
                  ).length;
                  return (
                    <div key={credential.id} className="px-5 py-4 sm:px-6">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-ink-900 dark:text-white">
                              {credential.profile_label}
                            </span>
                            <StatusPill tone="neutral">{credential.provider}</StatusPill>
                            <StatusPill
                              tone={
                                credential.test_status === "valid"
                                  ? "success"
                                  : credential.test_status === "invalid"
                                    ? "warning"
                                    : "neutral"
                              }
                            >
                              {credential.test_status === "valid"
                                ? "已验证"
                                : credential.test_status === "invalid"
                                  ? "验证失败"
                                  : "未验证"}
                            </StatusPill>
                            {usedByCount > 0 && (
                              <StatusPill tone="neutral">{`${usedByCount} 个模型使用`}</StatusPill>
                            )}
                          </div>
                          <p className="mt-1 font-mono text-xs text-ink-500">{credential.masked_key}</p>
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <button
                            type="button"
                            className="btn-ghost"
                            onClick={() => {
                              setRotateId(credential.id);
                              setRotateKey("");
                            }}
                          >
                            <RefreshCw className="size-4" />
                            轮换
                          </button>
                          <button
                            type="button"
                            className="icon-btn text-cinnabar-600"
                            title="删除密钥"
                            aria-label={`删除密钥 ${credential.profile_label}`}
                            onClick={() => {
                              if (window.confirm(`确定删除密钥“${credential.profile_label}”吗？`)) {
                                deleteCredential.mutate(credential.id);
                              }
                            }}
                          >
                            <Trash2 className="size-4" />
                          </button>
                        </div>
                      </div>
                      {rotateId === credential.id && (
                        <form
                          className="mt-4 flex flex-col gap-2 sm:flex-row"
                          onSubmit={(event) => {
                            event.preventDefault();
                            rotateCredential.mutate({ id: credential.id, apiKey: rotateKey.trim() });
                          }}
                        >
                          <input
                            type="password"
                            autoComplete="new-password"
                            className="field flex-1 font-mono"
                            value={rotateKey}
                            onChange={(event) => setRotateKey(event.target.value)}
                            placeholder="输入新的 API Key"
                            aria-label={`轮换 ${credential.profile_label} 的 API Key`}
                            required
                          />
                          <button type="submit" className="btn-primary" disabled={rotateCredential.isPending}>
                            <Save className="size-4" />
                            保存新密钥
                          </button>
                          <button type="button" className="icon-btn" onClick={() => setRotateId(null)}>
                            <X className="size-4" />
                            <span className="sr-only">取消轮换</span>
                          </button>
                        </form>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </details>
      </ServerSection>

      <ServerSection
        icon={Sparkles}
        title="翻译提示词"
        description="创建不同文体的系统提示词，默认模板会用于新书翻译。"
        action={
          <button
            type="button"
            className="btn-secondary shrink-0"
            onClick={() => {
              setTemplateDraft(emptyTemplate);
              setTemplateEditId(null);
              setTemplateFormOpen(true);
              setPreviewText("");
            }}
          >
            <Plus className="size-4" />
            新建模板
          </button>
        }
      >
        {templateFormOpen && (
          <form onSubmit={submitTemplate} className="border-b hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:px-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="field-label" htmlFor="template-name">模板名称</label>
                <input
                  id="template-name"
                  className="field"
                  value={templateDraft.name}
                  onChange={(event) =>
                    setTemplateDraft((current) => ({ ...current, name: event.target.value }))
                  }
                  placeholder="例如：文学小说"
                  required
                />
              </div>
              <div>
                <label className="field-label" htmlFor="template-description">说明</label>
                <input
                  id="template-description"
                  className="field"
                  value={templateDraft.description}
                  onChange={(event) =>
                    setTemplateDraft((current) => ({ ...current, description: event.target.value }))
                  }
                  placeholder="模板的适用场景"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="template-system">系统提示词</label>
                <textarea
                  id="template-system"
                  className="field min-h-36 resize-y"
                  value={templateDraft.systemPrompt}
                  onChange={(event) =>
                    setTemplateDraft((current) => ({ ...current, systemPrompt: event.target.value }))
                  }
                  required
                />
              </div>
              <div className="sm:col-span-2">
                <label className="field-label" htmlFor="template-prefix">附加要求（可选）</label>
                <textarea
                  id="template-prefix"
                  className="field min-h-24 resize-y"
                  value={templateDraft.userPrefix}
                  onChange={(event) =>
                    setTemplateDraft((current) => ({ ...current, userPrefix: event.target.value }))
                  }
                  placeholder="例如：保留章节标题、编号和专有名词。"
                />
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink-500">
              <span>可用占位符：</span>
              {(runtimeQuery.data?.prompt_placeholders ?? []).map((placeholder) => (
                <code key={placeholder} className="rounded bg-ink-100 px-1.5 py-0.5 dark:bg-ink-800">
                  {`{${placeholder}}`}
                </code>
              ))}
            </div>
            <div className="mt-4 flex flex-col gap-3 border-t hairline pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                  <input
                    type="checkbox"
                    className="size-4 accent-cinnabar-700"
                    checked={templateDraft.enabled}
                    onChange={(event) =>
                      setTemplateDraft((current) => ({ ...current, enabled: event.target.checked }))
                    }
                  />
                  启用
                </label>
                <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                  <input
                    type="checkbox"
                    className="size-4 accent-cinnabar-700"
                    checked={templateDraft.isDefault}
                    onChange={(event) =>
                      setTemplateDraft((current) => ({ ...current, isDefault: event.target.checked }))
                    }
                  />
                  设为默认
                </label>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={previewTemplate.isPending || !templateDraft.systemPrompt.trim()}
                  onClick={() =>
                    previewTemplate.mutate({
                      system_prompt: templateDraft.systemPrompt,
                      user_prefix: templateDraft.userPrefix || null,
                      source_lang: "en",
                      target_lang: "zh-CN",
                    })
                  }
                >
                  <FlaskConical className="size-4" />
                  预览
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setTemplateFormOpen(false);
                    setTemplateEditId(null);
                    setPreviewText("");
                  }}
                >
                  取消
                </button>
                <button type="submit" className="btn-primary" disabled={saveTemplate.isPending}>
                  <Save className="size-4" />
                  {templateEditId === null ? "创建模板" : "保存修改"}
                </button>
              </div>
            </div>
            {previewText && (
              <div className="mt-4 border-t hairline pt-4">
                <div className="mb-2 flex items-center gap-2 text-xs font-medium text-ink-600 dark:text-ink-300">
                  <Check className="size-3.5 text-emerald-600" />
                  英文 → 简体中文预览
                </div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-ink-950 p-4 text-xs leading-6 text-ink-100">
                  {previewText}
                </pre>
              </div>
            )}
          </form>
        )}
        {templatesQuery.isLoading ? (
          <EmptyMessage>正在读取提示词模板...</EmptyMessage>
        ) : templates.length === 0 ? (
          <EmptyMessage>尚未创建提示词模板。</EmptyMessage>
        ) : (
          <div className="divide-y hairline">
            {templates.map((template) => (
              <div key={template.id} className="px-5 py-4 sm:px-6">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink-900 dark:text-white">{template.name}</span>
                      {template.is_default && <StatusPill tone="success"><Star className="size-3" />默认</StatusPill>}
                      {template.is_builtin && <StatusPill tone="neutral">内置</StatusPill>}
                      {!template.enabled && <StatusPill tone="warning">已停用</StatusPill>}
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-ink-500">
                      {template.description || template.system_prompt}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {!template.is_default && (
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() => setDefaultTemplate.mutate(template.id)}
                      >
                        <Star className="size-4" />
                        设为默认
                      </button>
                    )}
                    <button
                      type="button"
                      className="icon-btn"
                      title="编辑提示词模板"
                      aria-label={`编辑 ${template.name}`}
                      onClick={() => {
                        setTemplateDraft(templateToDraft(template));
                        setTemplateEditId(template.id);
                        setTemplateFormOpen(true);
                        setPreviewText("");
                      }}
                    >
                      <Pencil className="size-4" />
                    </button>
                    <button
                      type="button"
                      className="icon-btn text-cinnabar-600"
                      title="删除提示词模板"
                      aria-label={`删除 ${template.name}`}
                      disabled={template.is_default}
                      onClick={() => {
                        if (window.confirm(`确定删除提示词模板“${template.name}”吗？`)) {
                          deleteTemplate.mutate(template.id);
                        }
                      }}
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </ServerSection>
    </div>
  );
}
