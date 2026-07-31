import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ModelProfile, Project, PromptTemplate } from "../../api/types";
import { ProjectConfigDialog } from "../WorkbenchDialogs";

const { modelProfilesMock, promptTemplatesMock } = vi.hoisted(() => ({
  modelProfilesMock: vi.fn(),
  promptTemplatesMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: {
    modelProfiles: modelProfilesMock,
    promptTemplates: promptTemplatesMock,
  },
  errorMessage: () => "请求失败",
  exportUrl: () => "",
}));

const profiles: ModelProfile[] = [
  {
    id: 1,
    display_name: "Gemini 国内",
    provider: "custom",
    litellm_model_id: "gemini-3.1-pro-low",
    credential_id: null,
    base_url: "https://relay.example.com/v1",
    enabled: true,
    is_default: true,
    max_concurrency: 4,
    context_window_tokens: 128000,
    max_output_tokens: 4096,
    generation_params: {},
    input_price_per_million: null,
    output_price_per_million: null,
    cache_read_price_per_million: null,
    cache_write_price_per_million: null,
    insecure_transport: false,
    created_at: "",
    updated_at: "",
  },
  {
    id: 2,
    display_name: "停用模型",
    provider: "openai",
    litellm_model_id: "openai/gpt-5-mini",
    credential_id: null,
    base_url: null,
    enabled: false,
    is_default: false,
    max_concurrency: 4,
    context_window_tokens: 128000,
    max_output_tokens: 4096,
    generation_params: {},
    input_price_per_million: null,
    output_price_per_million: null,
    cache_read_price_per_million: null,
    cache_write_price_per_million: null,
    insecure_transport: false,
    created_at: "",
    updated_at: "",
  },
];

const templates: PromptTemplate[] = [
  {
    id: 10,
    name: "文学小说",
    description: null,
    system_prompt: "Translate {source_lang} to {target_lang}.",
    user_prefix: null,
    enabled: true,
    is_default: true,
    is_builtin: true,
    created_at: "",
    updated_at: "",
  },
  {
    id: 11,
    name: "停用模板",
    description: null,
    system_prompt: "x",
    user_prefix: null,
    enabled: false,
    is_default: false,
    is_builtin: false,
    created_at: "",
    updated_at: "",
  },
];

const baseProject = {
  id: 7,
  title: "Sample Book",
  source_lang: "en",
  target_lang: "zh-CN",
  source_type: "md",
  status: "ready",
  provider_cfg: { model: "openai/gpt-5-mini" },
  model_profile_id: null,
  prompt_template_id: null,
} as Project;

function renderDialog(project: Project = baseProject, onSave = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ProjectConfigDialog
        open
        project={project}
        pending={false}
        onClose={() => {}}
        onSave={onSave}
      />
    </QueryClientProvider>,
  );
  return onSave;
}

describe("ProjectConfigDialog", () => {
  it("renders server-side profile and template selectors with enabled options only", async () => {
    modelProfilesMock.mockResolvedValue(profiles);
    promptTemplatesMock.mockResolvedValue(templates);
    renderDialog();

    const profileSelect = await screen.findByLabelText("模型配置");
    expect(profileSelect).toHaveValue("");
    expect(modelProfilesMock).toHaveBeenCalled();
    expect(promptTemplatesMock).toHaveBeenCalled();

    expect(await screen.findByRole("option", { name: "Gemini 国内（默认）" })).toBeInTheDocument();
    const profileOptions = screen.getAllByRole("option");
    const profileLabels = profileOptions.map((option) => option.textContent);
    expect(profileLabels).toContain("跟随后端默认（环境变量）");
    expect(profileLabels).not.toContain("停用模型");

    const templateSelect = await screen.findByLabelText("提示词模板");
    expect(templateSelect).toHaveValue("");
    expect(await screen.findByRole("option", { name: "文学小说（默认）" })).toBeInTheDocument();
    const templateOptions = screen.getAllByRole("option");
    const templateLabels = templateOptions.map((option) => option.textContent);
    expect(templateLabels).toContain("跟随后端默认模板");
    expect(templateLabels).not.toContain("停用模板");
  });

  it("sends the chosen profile and template on save", async () => {
    modelProfilesMock.mockResolvedValue(profiles);
    promptTemplatesMock.mockResolvedValue(templates);
    const onSave = renderDialog();
    const user = userEvent.setup();

    await screen.findByRole("option", { name: "Gemini 国内（默认）" });
    await user.selectOptions(screen.getByLabelText("模型配置"), "1");
    await user.selectOptions(screen.getByLabelText("提示词模板"), "10");
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          model_profile_id: 1,
          prompt_template_id: 10,
        }),
      ),
    );
  });

  it("clears an existing assignment back to the backend default", async () => {
    modelProfilesMock.mockResolvedValue(profiles);
    promptTemplatesMock.mockResolvedValue(templates);
    const boundProject = {
      ...baseProject,
      model_profile_id: 1,
      prompt_template_id: 10,
    } as Project;
    const onSave = renderDialog(boundProject);
    const user = userEvent.setup();

    await screen.findByRole("option", { name: "Gemini 国内（默认）" });
    expect(screen.getByLabelText("模型配置")).toHaveValue("1");
    expect(screen.getByLabelText("提示词模板")).toHaveValue("10");

    await user.selectOptions(screen.getByLabelText("模型配置"), "");
    await user.selectOptions(screen.getByLabelText("提示词模板"), "");
    await user.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          model_profile_id: null,
          prompt_template_id: null,
        }),
      ),
    );
  });
});
