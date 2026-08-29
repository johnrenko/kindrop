import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
    source_type: "drive",
    name: `Naruto ${index}.cbz`,
    path: `Naruto/Naruto ${index}.cbz`,
    size: 1024,
    fingerprint: `fp-${index}`,
    optimize: true,
  };
}

const candidates = [1, 2, 3, 4, 5].map(makeCandidate);

const payloads: Record<string, unknown> = {
  "/api/candidates": candidates,
  "/api/candidate-series": [],
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
  it("adds browser-selected local archives to review", async () => {
    const uploaded = { ...makeCandidate(6), name: "Local Guide.pdf", path: "Local uploads/Local Guide.pdf" };
    const uploadedBodies: FormData[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (String(input) === "/api/local-files") {
        uploadedBodies.push(init?.body as FormData);
        return Response.json(uploaded, { status: 201 });
      }
      return Response.json(payloads[String(input)]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ReviewPage /></QueryClientProvider>);

    await userEvent.upload(screen.getByLabelText("Choose local comic archives"), new File(["comic"], "Local Guide.pdf", { type: "application/pdf" }));

    await screen.findByText("1 local archive is ready for review.");
    expect(uploadedBodies).toHaveLength(1);
    expect(uploadedBodies[0].get("file")).toBeInstanceOf(File);
  });

  it("selects every new candidate by default", async () => {
    renderReview();
    const boxes = await findCheckboxes();

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([
      true,
      true,
      true,
      true,
      true,
    ]);
    expect(screen.getByText("5 selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Optimize & send 5/i })).toBeEnabled();
  });

  it("selects a range with shift+click", async () => {
    renderReview();
    const boxes = await findCheckboxes();
    expect(boxes).toHaveLength(5);

    const user = userEvent.setup();
    await user.click(boxes[0]);
    await user.keyboard("{Shift>}");
    await user.click(boxes[3]);
    await user.keyboard("{/Shift}");

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([false, false, false, false, true]);
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("deselects a range with shift+click on a selected line", async () => {
    renderReview();
    const boxes = await findCheckboxes();
    const user = userEvent.setup();

    // Everything starts selected, then shift-click-deselect lines 2..4.
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

    expect(boxes.map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, false, true, true]);
  });
});

describe("Review preparation choices", () => {
  it("optimizes PDF by default and identifies EPUB passthrough", async () => {
    const mixed = [
      { ...makeCandidate(1), name: "Guide.pdf", path: "Docs/Guide.pdf" },
      { ...makeCandidate(2), name: "Novel.epub", path: "Books/Novel.epub" },
    ];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/candidates") return Response.json(mixed);
      return Response.json(payloads[String(input)]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ReviewPage />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("checkbox", { name: "Optimize Guide.pdf" })).toBeChecked();
    expect(screen.getByText("EPUB · Sends unchanged")).toBeInTheDocument();
  });

  it("matches series metadata once and applies it to every detected volume", async () => {
    const seriesCandidates = [18, 19].map((index) => ({
      ...makeCandidate(index),
      resolved_title: `Blue Lock ${index}`,
      metadata: { title: null, series: null, number: null, author: null, cover_url: null },
      name: `Blue Lock ${index}.cbz`,
      path: `Blue Lock ${index}.cbz`,
    }));
    const requestBodies: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/candidates") return Response.json(seriesCandidates);
      if (path === "/api/settings") return Response.json(payloads[path]);
      if (path === "/api/candidate-series" && init?.method === "PATCH") {
        requestBodies.push(JSON.parse(String(init.body)));
        return Response.json(seriesCandidates);
      }
      if (path === "/api/candidate-series") {
        return Response.json([
          {
            id: "series:blue-lock",
            suggested_series: "Blue Lock",
            confidence: "high",
            ready_count: 2,
            known_count: 2,
            first_volume: 18,
            last_volume: 19,
            missing_volumes: [],
            duplicate_volumes: [],
            members: seriesCandidates.map((candidate, offset) => ({
              candidate_id: candidate.id,
              name: candidate.name,
              number: 18 + offset,
            })),
          },
        ]);
      }
      if (path === "/api/metadata/search?query=Blue%20Lock") {
        return Response.json([
          {
            anilist_id: 49596,
            title: "Blue Lock",
            native_title: "ブルーロック",
            author: "Muneyuki Kaneshiro",
            cover_url: "https://img.anili.st/blue-lock.jpg",
            format: "MANGA",
            year: 2018,
          },
        ]);
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ReviewPage />
      </QueryClientProvider>,
    );

    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Smart series import" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Match metadata for Blue Lock" }));
    await user.click(screen.getByRole("button", { name: "Search AniList" }));
    await user.click(
      await screen.findByRole("button", { name: "Apply Blue Lock to 2 candidates" }),
    );

    await waitFor(() => expect(requestBodies).toEqual([
      {
        group_id: "series:blue-lock",
        candidate_ids: ["cand-18", "cand-19"],
        series: "Blue Lock",
        author: "Muneyuki Kaneshiro",
        cover_url: "https://img.anili.st/blue-lock.jpg",
      },
    ]));
  });
});
