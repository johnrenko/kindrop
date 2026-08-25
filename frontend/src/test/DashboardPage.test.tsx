import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { DashboardPage } from "../pages/DashboardPage";

vi.mock("@tanstack/react-router", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...original,
    Link: ({ children, to, className }: PropsWithChildren<{ to: string; className?: string }>) => (
      <a href={to} className={className}>{children}</a>
    ),
  };
});

const payloads: Record<string, unknown> = {
  "/api/setup/status": {
    client_configured: true,
    google_connected: true,
    google_email: "reader@example.com",
    source_folder_configured: true,
    kindle_destination_configured: true,
    ready: true,
  },
  "/api/settings": {
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
    catalog_username: null,
    catalog_password: null,
  },
  "/api/scans": [],
  "/api/jobs": [],
  "/api/candidates": [],
  "/api/library": {
    ready_count: 12,
    pending_count: 2,
    failed_count: 1,
    total_bytes: 314572800,
    failures: [{ id: "lf-1", title: "Broken, Ch. 1", error: "boom" }],
  },
};

describe("Dashboard", () => {
  it("starts a manual scan from a configured desk", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/scans" && init?.method === "POST") {
        return Response.json({
          id: "scan-1",
          status: "queued",
          progress: 0,
          discovered_count: 0,
          processed_count: 0,
          error: null,
          created_at: new Date().toISOString(),
          completed_at: null,
        });
      }
      return Response.json(payloads[path]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DashboardPage />
      </QueryClientProvider>,
    );

    const scan = await screen.findByRole("button", { name: "Scan source folder" });
    await userEvent.click(scan);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scans",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("pauses and stops a running scan", async () => {
    const runningScan = {
      id: "scan-1",
      status: "scanning",
      progress: 40,
      discovered_count: 5,
      processed_count: 2,
      error: null,
      created_at: new Date().toISOString(),
      completed_at: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (init?.method === "POST") return Response.json({ status: "accepted" });
      if (path === "/api/scans") return Response.json([runningScan]);
      return Response.json(payloads[path]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DashboardPage />
      </QueryClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Pause scan" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stop scan" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scans/scan-1/pause",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scans/scan-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("offers to resume a paused scan", async () => {
    const pausedScan = {
      id: "scan-2",
      status: "paused",
      progress: 40,
      discovered_count: 5,
      processed_count: 2,
      error: null,
      created_at: new Date().toISOString(),
      completed_at: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (init?.method === "POST") return Response.json({ status: "queued" });
      if (path === "/api/scans") return Response.json([pausedScan]);
      return Response.json(payloads[path]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DashboardPage />
      </QueryClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Resume scan" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scans/scan-2/resume",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows the Library mirror status", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path in payloads) return Response.json(payloads[path]);
      throw new Error(`Unexpected request: ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DashboardPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/12 ready/i)).toBeInTheDocument();
    expect(screen.getByText(/300 MB/)).toBeInTheDocument();
    expect(screen.getByText(/2 mirroring/i)).toBeInTheDocument();
    expect(screen.getByText(/1 failed/i)).toBeInTheDocument();
  });
});
