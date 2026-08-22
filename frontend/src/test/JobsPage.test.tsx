import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { JobsPage } from "../pages/JobsPage";

const job = {
  id: "job-1",
  batch_id: "batch-1",
  status: "sent",
  title: "Naruto, Ch. 700",
  preset: {
    kindle_profile: "KPW5",
    reading_direction: "rtl",
    spread_mode: "both",
    crop_mode: "margins_and_page_numbers",
  },
  merged_count: null,
  progress: 100,
  error: null,
  created_at: new Date().toISOString(),
  completed_at: new Date().toISOString(),
  deliveries: [
    {
      id: "delivery-1",
      status: "unknown",
      filename: "Naruto, Ch. 700.epub",
      part_number: 1,
      total_parts: 2,
      gmail_message_id: null,
      error_code: null,
      error_detail: "Kindrop is checking Gmail's Sent folder and will resend automatically.",
      verification_url: null,
      sent_at: new Date().toISOString(),
    },
    {
      id: "delivery-2",
      status: "failed",
      filename: "Naruto, Ch. 700 - Part 2.epub",
      part_number: 2,
      total_parts: 2,
      gmail_message_id: null,
      error_code: null,
      error_detail: "The message never appeared in Gmail's Sent folder after 3 sends.",
      verification_url: null,
      sent_at: new Date().toISOString(),
    },
  ],
};

function renderJobs() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <JobsPage />
    </QueryClientProvider>,
  );
}

describe("Jobs", () => {
  it("shows a verifying badge and keeps the manual resend available", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json([job]));
    renderJobs();

    expect((await screen.findAllByText("Verifying…")).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/checking Gmail's Sent folder/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Resend with same settings" })).toHaveLength(2);
  });

  it("shows the failure detail and resends the failed delivery", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (init?.method === "POST") return Response.json({ id: "job-2", status: "queued" });
      return Response.json([job]);
    });
    renderJobs();

    expect(
      await screen.findByText(/never appeared in Gmail's Sent folder/),
    ).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Resend with same settings" });
    await userEvent.click(buttons[1]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/deliveries/delivery-2/resend",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("keeps simple retry as the primary job action", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method === "POST") return Response.json({ id: "job-2", status: "queued" });
      return Response.json([job]);
    });
    renderJobs();

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Retry with different settings" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/retry",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ preset: job.preset }),
      }),
    );
  });

  it("queues a sent job again with corrected conversion settings", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (input === "/api/kindle-profiles") {
        return Response.json([
          { id: "KPW5", name: "Kindle Paperwhite 5" },
          { id: "KPW6", name: "Kindle Paperwhite 6" },
        ]);
      }
      if (init?.method === "POST") return Response.json({ id: "job-2", status: "queued" });
      return Response.json([job]);
    });
    renderJobs();

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Retry with different settings" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "More retry options for Naruto, Ch. 700" }));
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Retry with different settings" }),
    );
    expect(screen.getByRole("dialog", { name: "Convert and send again" })).toBeInTheDocument();
    expect(screen.getByText(/earlier Kindle copy is not removed/i)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Kindle profile"), "KPW6");
    await userEvent.selectOptions(screen.getByLabelText("Reading direction"), "ltr");
    await userEvent.selectOptions(screen.getByLabelText("Crop behavior"), "none");
    await userEvent.click(screen.getByRole("button", { name: "Queue corrected job" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/retry",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          preset: {
            kindle_profile: "KPW6",
            reading_direction: "ltr",
            spread_mode: "both",
            crop_mode: "none",
          },
        }),
      }),
    );
  });
});
