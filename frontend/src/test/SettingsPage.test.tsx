import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../pages/SettingsPage";

const baseSettings = {
  google_email: "reader@example.com",
  source_folder_id: "drive-folder",
  source_folder_name: "Manga inbox",
  kindle_email: "reader@kindle.com",
  preset: {
    kindle_profile: "KPW5",
    reading_direction: "rtl",
    spread_mode: "both",
    crop_mode: "margins_and_page_numbers",
  },
  catalog_enabled: false,
  catalog_username: null as string | null,
  catalog_password: null as string | null,
};

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

describe("Settings — Catalog", () => {
  it("shows the generated catalog credentials right after enabling, without a reload", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/setup/status") {
        return Response.json({
          client_configured: true,
          google_connected: true,
          google_email: "reader@example.com",
          source_folder_configured: true,
          kindle_destination_configured: true,
          ready: true,
        });
      }
      if (path === "/api/settings" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as typeof baseSettings;
        // Simulate the backend: it generates credentials on first enable.
        return Response.json({
          ...body,
          catalog_username: body.catalog_enabled ? "kindle" : null,
          catalog_password: body.catalog_enabled ? "generated-pw" : null,
        });
      }
      if (path === "/api/settings") return Response.json(baseSettings);
      if (path === "/api/kindle-profiles") {
        return Response.json([{ id: "KPW5", name: "Kindle Paperwhite 5" }]);
      }
      if (path.startsWith("/api/drive/folders")) {
        return Response.json({ folders: [], next_page_token: null });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    renderSettings();

    const toggle = await screen.findByRole("checkbox", { name: /enable catalog/i });
    expect(screen.queryByText("kindle")).not.toBeInTheDocument();
    expect(screen.queryByText(/In KOReader/i)).not.toBeInTheDocument();

    await userEvent.click(toggle);

    expect(await screen.findByText("kindle")).toBeInTheDocument();
    expect(screen.getByText("generated-pw")).toBeInTheDocument();
    expect(screen.getByText(/In KOReader/i)).toBeInTheDocument();
  });
});
