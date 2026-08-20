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
    expect(screen.getAllByRole("button", { name: "Resend as a new job" })).toHaveLength(2);
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
    const buttons = screen.getAllByRole("button", { name: "Resend as a new job" });
    await userEvent.click(buttons[1]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/deliveries/delivery-2/resend",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
