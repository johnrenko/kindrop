import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewPage } from "../pages/ReviewPage";
import type { Candidate } from "../types";

function makeCandidate(index: number): Candidate {
  return {
    id: `cand-${index}`,
    status: "ready",
    resolved_title: `Naruto, Ch. ${String(index).padStart(3, "0")}`,
    title_override: null,
    metadata: { series: "Naruto", number: String(index), author: "Masashi Kishimoto", cover_url: null },
    cache_expires_at: null,
    error: null,
    drive_file_id: `drive-${index}`,
    name: `Naruto ${index}.cbz`,
    path: `Naruto/Naruto ${index}.cbz`,
    size: 1024,
    fingerprint: `fp-${index}`,
  };
}

const candidates = [1, 2, 3, 4, 5].map(makeCandidate);

const payloads: Record<string, unknown> = {
  "/api/candidates": candidates,
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
  },
};

function renderReview() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => Response.json(payloads[String(input)]));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReviewPage />
    </QueryClientProvider>,
  );
}

async function findCheckboxes() {
  await screen.findByText("Select all 5");
  const boxes = screen.getAllByRole("checkbox");
  // Drop the "Select all" checkbox and the merge-by-volume toggle.
  return boxes.filter((box) => box.closest(".candidate__check") !== null);
}

describe("Review selection", () => {
  it("selects a range with shift+click", async () => {
    renderReview();
    const boxes = await findCheckboxes();
    expect(boxes).toHaveLength(5);

    const user = userEvent.setup();
    await user.click(boxes[0]);
    await user.keyboard("{Shift>}");
    await user.click(boxes[3]);
    await user.keyboard("{/Shift}");

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, true, true, false]);
    expect(screen.getByText("4 selected")).toBeInTheDocument();
  });

  it("deselects a range with shift+click on a selected line", async () => {
    renderReview();
    const boxes = await findCheckboxes();
    const user = userEvent.setup();

    // Select everything, then shift-click-deselect lines 2..4.
    const selectAll = screen.getAllByRole("checkbox").find((box) => box.closest(".check-all") !== null)!;
    await user.click(selectAll);
    await user.click(boxes[1]);
    expect((boxes[1] as HTMLInputElement).checked).toBe(false);
    await user.keyboard("{Shift>}");
    await user.click(boxes[3]);
    await user.keyboard("{/Shift}");

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, false, false, false, true]);
  });

  it("falls back to a single toggle when no anchor exists", async () => {
    renderReview();
    const boxes = await findCheckboxes();
    const user = userEvent.setup();

    await user.keyboard("{Shift>}");
    await user.click(boxes[2]);
    await user.keyboard("{/Shift}");

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([false, false, true, false, false]);
  });
});
