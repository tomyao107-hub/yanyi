import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../../store/settings";
import { SettingsPage } from "../SettingsPage";

vi.mock("../../components/ServerSettings", () => ({
  ServerSettings: () => <form aria-label="服务器配置" />,
}));

vi.mock("../../store/settings", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../store/settings")>();
  return {
    ...original,
    useSettings: () => ({
      settings: original.defaultSettings,
      providerSettingsResolved: true,
      updateSettings: vi.fn(),
      replaceSettings: vi.fn(),
      resetSettings: () => original.defaultSettings,
    }),
  };
});

vi.mock("../../store/toast", () => ({
  useToast: () => ({ notify: vi.fn() }),
}));

describe("SettingsPage", () => {
  it("keeps server configuration forms outside the local-settings form", () => {
    render(<SettingsPage />);

    const localSettingsForm = document.getElementById("settings-form");
    const serverSettingsForm = screen.getByRole("form", { name: "服务器配置" });

    expect(localSettingsForm).toBeInTheDocument();
    expect(localSettingsForm).not.toContainElement(serverSettingsForm);
    expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument();
  });
});
