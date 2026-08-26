import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../pages/SettingsPage";

const settings = {
  google_email: "reader@example.com",
  source_folder_id: "drive-folder",
  source_folder_name: "Manga inbox",
  kindle_email: "reader@kindle.com",
  ssh_host: "192.168.1.53",
  ssh_port: 2222,
  ssh_destination: "/mnt/us/documents/KOReader/Kindrop",
  preset: {
    kindle_profile: "KPW5",
    reading_direction: "rtl",
    spread_mode: "both",
    crop_mode: "margins_and_page_numbers",
  },
};

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

describe("Settings", () => {
  it("presents SSH as the primary Kindle destination and tests it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/ssh/test" && init?.method === "POST") {
        return Response.json({
          configured: true,
          reachable: true,
          host: "192.168.1.53",
          port: 2222,
          destination: "/mnt/us/documents/KOReader",
          free_bytes: 784_097_280,
          capacity_unknown: false,
          detail: "Kindle is ready",
          tested_at: new Date().toISOString(),
        });
      }
      if (path === "/api/ssh/status") {
        return Response.json({
          configured: true,
          reachable: null,
          host: "192.168.1.53",
          port: 2222,
          destination: "/mnt/us/documents/KOReader",
          free_bytes: null,
          capacity_unknown: true,
          detail: null,
          tested_at: null,
        });
      }
      if (path === "/api/settings") return Response.json(settings);
      if (path === "/api/kindle-profiles") {
        return Response.json([{ id: "KPW5", name: "Kindle Paperwhite 5" }]);
      }
      if (path.startsWith("/api/drive/folders?")) return Response.json({ folders: [], next_page_token: null });
      return Response.json({
        client_configured: true,
        google_connected: true,
        google_email: "reader@example.com",
        source_folder_configured: true,
        kindle_destination_configured: true,
        ssh_destination_configured: true,
        ready: true,
      });
    });
    renderSettings();

    expect(await screen.findByRole("heading", { name: "Send over Wi-Fi" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("192.168.1.53")).toBeInTheDocument();
    expect(screen.getByText("Manual email fallback")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Test Kindle connection" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ssh/test",
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByText("Kindle is ready")).toBeInTheDocument();
    expect(screen.getByText(/748 MB free/i)).toBeInTheDocument();
  });
});
