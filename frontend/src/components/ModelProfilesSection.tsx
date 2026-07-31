import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  FlaskConical,
  Pencil,
  Plus,
  Save,
  Server,
  Star,
  Trash2,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { ModelProfile, ModelProfileInput, ProviderOption } from "../api/types";
import { useToast } from "../store/toast";
import { EmptyMessage, ServerSection, StatusPill } from "./ServerSettingsShared";

interface ProfileDraft {
  displayName: string;
  provider: string;
  modelId: string;
  credentialId: string;
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
  credentialId: "",
  baseUrl: "",
  maxConcurrency: 4,
  contextWindowTokens: 128000,
  maxOutputTokens: 4096,
  generationParams: "{}",
  enabled: true,
  isDefault: false,
};

function profileToDraft(profile: ModelProfile): ProfileDraft {
  return {
    displayName: profile.display_name,
    provider: profile.provider,
    modelId: profile.litellm_model_id,
    credentialId: profile.credential_id === null ? "" : String(profile.credential_id),
    baseUrl: profile.base_url ?? "",
    maxConcurrency: profile.max_concurrency,
    contextWindowTokens: profile.context_window_tokens,
    maxOutputTokens: profile.max_output_tokens,
    generationParams: JSON.stringify(profile.generation_params ?? {}, null, 2),
    enabled: profile.enabled,
    isDefault: profile.is_default,
  };
}

export function ModelProfilesSection({
  providers,
  suggestedModels,
  defaultModel,
  connectionTestNotice,
}: {
  providers: ProviderOption[];
  suggestedModels: string[];
  defaultModel: string;
  connectionTestNotice: string | null;
}) {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [draft, setDraft] = useState(emptyProfile);

  const credentialsQuery = useQuery({
    queryKey: queryKeys.providerCredentials,
    queryFn: api.providerCredentials,
  });
  const profilesQuery = useQuery({
    queryKey: queryKeys.modelProfiles,
    queryFn: api.modelProfiles,
  });
  const credentials = credentialsQuery.data ?? [];
  const profiles = profilesQuery.data ?? [];
  const selectedProvider = providers.find((item) => item.name === draft.provider);
  const compatibleCredentials = useMemo(
    () => credentials.filter((item) => item.provider === draft.provider),
    [credentials, draft.provider],
  );

  const refreshProfiles = () => queryClient.invalidateQueries({ queryKey: queryKeys.modelProfiles });
  const refreshCredentials = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.providerCredentials });

  const saveProfile = useMutation({
    mutationFn: (input: ModelProfileInput) =>
      editId === null
        ? api.createModelProfile(input)
        : api.updateModelProfile(editId, input),
    onSuccess: () => {
      void refreshProfiles();
      setDraft(emptyProfile);
      setEditId(null);
      setFormOpen(false);
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

  const openNewProfile = () => {
    setDraft({ ...emptyProfile, modelId: defaultModel });
    setEditId(null);
    setFormOpen(true);
  };

  const submitProfile = (event: FormEvent) => {
    event.preventDefault();
    let generationParams: Record<string, unknown>;
    try {
      const parsed = JSON.parse(draft.generationParams || "{}") as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("生成参数必须是 JSON 对象。");
      }
      generationParams = parsed as Record<string, unknown>;
    } catch (error) {
      notify(error instanceof Error ? error.message : "生成参数不是有效 JSON。", "error");
      return;
    }
    saveProfile.mutate({
      display_name: draft.displayName.trim(),
      provider: draft.provider,
      litellm_model_id: draft.modelId.trim(),
      credential_id: draft.credentialId ? Number(draft.credentialId) : null,
      base_url: draft.baseUrl.trim() || null,
      enabled: draft.enabled,
      is_default: draft.isDefault,
      max_concurrency: draft.maxConcurrency,
      context_window_tokens: draft.contextWindowTokens,
      max_output_tokens: draft.maxOutputTokens,
      generation_params: generationParams,
    });
  };

  return (
    <ServerSection
      icon={Server}
      title="模型配置"
      description="每个模型可独立设置供应商、API 地址、凭据与并发参数，支持多个供应商共存。"
      action={
        <button type="button" className="btn-secondary shrink-0" onClick={openNewProfile}>
          <Plus className="size-4" />
          添加模型
        </button>
      }
    >
      {formOpen && (
        <form onSubmit={submitProfile} className="border-b hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:px-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="field-label" htmlFor="profile-name">配置名称</label>
              <input
                id="profile-name"
                className="field"
                value={draft.displayName}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, displayName: event.target.value }))
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
                value={draft.provider}
                onChange={(event) =>
                  setDraft((current) => ({
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
                value={draft.modelId}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, modelId: event.target.value }))
                }
                placeholder="openai/gemini-3.1-pro-low"
                required
              />
              <datalist id="server-model-suggestions">
                {suggestedModels.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
            </div>
            <div>
              <label className="field-label" htmlFor="profile-credential">API 凭据</label>
              <select
                id="profile-credential"
                className="field"
                value={draft.credentialId}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, credentialId: event.target.value }))
                }
              >
                <option value="">不使用已保存凭据</option>
                {compatibleCredentials.map((credential) => (
                  <option key={credential.id} value={credential.id}>
                    {credential.profile_label}（{credential.masked_key}）
                  </option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="profile-url">
                API 地址{selectedProvider?.requires_base_url ? "（必填）" : "（可选）"}
              </label>
              <input
                id="profile-url"
                type="url"
                className="field font-mono"
                value={draft.baseUrl}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, baseUrl: event.target.value }))
                }
                placeholder="https://example.com/v1"
                required={selectedProvider?.requires_base_url}
              />
              {draft.baseUrl.startsWith("http://") &&
                !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::|\/|$)/i.test(draft.baseUrl) && (
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
                value={draft.maxConcurrency}
                onChange={(event) =>
                  setDraft((current) => ({
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
                value={draft.maxOutputTokens}
                onChange={(event) =>
                  setDraft((current) => ({
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
                value={draft.contextWindowTokens}
                onChange={(event) =>
                  setDraft((current) => ({
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
                value={draft.generationParams}
                onChange={(event) =>
                  setDraft((current) => ({
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
                  checked={draft.enabled}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, enabled: event.target.checked }))
                  }
                />
                启用
              </label>
              <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                <input
                  type="checkbox"
                  className="size-4 accent-cinnabar-700"
                  checked={draft.isDefault}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, isDefault: event.target.checked }))
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
                  setFormOpen(false);
                  setEditId(null);
                }}
              >
                取消
              </button>
              <button type="submit" className="btn-primary" disabled={saveProfile.isPending}>
                <Save className="size-4" />
                {editId === null ? "创建配置" : "保存修改"}
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
                        setDraft(profileToDraft(profile));
                        setEditId(profile.id);
                        setFormOpen(true);
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
      {connectionTestNotice && (
        <p className="border-t hairline px-5 py-3 text-xs leading-5 text-ink-500 sm:px-6">
          {connectionTestNotice}
        </p>
      )}
    </ServerSection>
  );
}
