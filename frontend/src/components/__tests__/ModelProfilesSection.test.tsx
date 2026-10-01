import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelProfilesSection } from "../ModelProfilesSection";

const mocks = vi.hoisted(() => ({
  credentials: vi.fn(), profiles: vi.fn(), createCredential: vi.fn(),
  createProfile: vi.fn(), notify: vi.fn(),
}));
vi.mock("../../api/client", () => ({
  api: {
    providerCredentials: mocks.credentials,
    modelProfiles: mocks.profiles,
    createProviderCredential: mocks.createCredential,
    createModelProfile: mocks.createProfile,
  },
  errorMessage: () => "请求失败",
}));
vi.mock("../../store/toast", () => ({ useToast: () => ({ notify: mocks.notify }) }));

async function openForm() {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ModelProfilesSection providers={[{ name: "custom", label: "自定义", hint: "", requires_base_url: true }]}
        suggestedModels={[]} defaultModel="openai/test" connectionTestNotice={null} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "添加模型" }));
  await user.type(screen.getByLabelText("配置名称"), "测试模型");
  await user.type(screen.getByLabelText("API 地址（必填）"), "https://example.com/v1");
  return user;
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.profiles.mockResolvedValue([]);
  mocks.credentials.mockResolvedValue([{ id: 7, provider: "custom", profile_label: "已有密钥", masked_key: "****" }]);
  mocks.createCredential.mockResolvedValue({ id: 8 });
  mocks.createProfile.mockResolvedValue({ id: 1 });
});

describe("inline model credentials", () => {
  it("encrypts a new key before binding it to the model", async () => {
    const user = await openForm();
    await user.type(screen.getByLabelText("API Key"), "test-secret");
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.createProfile).toHaveBeenCalledWith(expect.objectContaining({ credential_id: 8 })));
    expect(mocks.createCredential.mock.calls[0][0]).toEqual({ provider: "custom", profile_label: "测试模型", api_key: "test-secret" });
    expect(mocks.createCredential.mock.invocationCallOrder[0]).toBeLessThan(mocks.createProfile.mock.invocationCallOrder[0]);
  });

  it("reuses a saved key without creating another credential", async () => {
    const user = await openForm();
    await user.click(screen.getByRole("button", { name: "复用已保存" }));
    await user.selectOptions(screen.getByLabelText("选择已保存的 API 密钥"), "7");
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.createProfile).toHaveBeenCalledWith(expect.objectContaining({ credential_id: 7 })));
    expect(mocks.createCredential).not.toHaveBeenCalled();
  });

  it("permits a model without a stored key", async () => {
    const user = await openForm();
    await user.click(screen.getByRole("button", { name: "不使用密钥" }));
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.createProfile).toHaveBeenCalledWith(expect.objectContaining({ credential_id: null })));
    expect(mocks.createCredential).not.toHaveBeenCalled();
  });

  it("does not save a model when credential encryption fails", async () => {
    mocks.createCredential.mockRejectedValue(new Error("failed"));
    const user = await openForm();
    await user.type(screen.getByLabelText("API Key"), "test-secret");
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("请求失败", "error"));
    expect(mocks.createProfile).not.toHaveBeenCalled();
  });

  it("retries failed model saving with the already encrypted credential", async () => {
    mocks.createProfile.mockRejectedValueOnce(new Error("failed")).mockResolvedValue({ id: 1 });
    mocks.credentials.mockResolvedValue([{ id: 8, provider: "custom", profile_label: "测试模型", masked_key: "****" }]);
    const user = await openForm();
    await user.type(screen.getByLabelText("API Key"), "test-secret");
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("请求失败", "error"));
    await user.click(screen.getByRole("button", { name: "创建配置" }));
    await waitFor(() => expect(mocks.createProfile).toHaveBeenCalledTimes(2));
    expect(mocks.createCredential).toHaveBeenCalledTimes(1);
    expect(screen.queryByDisplayValue("test-secret")).not.toBeInTheDocument();
  });
});
