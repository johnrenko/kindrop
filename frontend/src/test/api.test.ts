import { describe, expect, it, vi } from "vitest";

import { api, formatBytes } from "../api";

describe("API client", () => {
  it("formats transfer sizes for review", () => {
    expect(formatBytes(800)).toBe("800 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024 * 1024)).toBe("20 MB");
  });

  it("surfaces the API error detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Choose a source folder first" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(api.startScan()).rejects.toThrow("Choose a source folder first");
  });

  it("clears history with a DELETE request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));

    await expect(api.clearHistory()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/history",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("cancels a job with a POST request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ status: "cancelled" }), { status: 200 }));

    await expect(api.cancelJob("job-1")).resolves.toEqual({ status: "cancelled" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("fetches the library mirror summary", async () => {
    const summary = {
      ready_count: 12,
      pending_count: 2,
      failed_count: 1,
      total_bytes: 314572800,
      failures: [{ id: "lf-1", title: "Broken, Ch. 1", error: "boom" }],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(summary));

    await expect(api.library()).resolves.toEqual(summary);

    expect(fetchMock).toHaveBeenCalledWith("/api/library", expect.objectContaining({}));
  });

  it("retries a failed library file with a POST request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ status: "queued" }), { status: 200 }));

    await expect(api.retryLibraryFile("lf-1")).resolves.toEqual({ status: "queued" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/library/lf-1/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("Batch creation", () => {
  it("sends the volume-merge flag with the selection", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "batch-1" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.createBatch(["c-1", "c-2"], {
      kindle_profile: "KPW34",
      reading_direction: "rtl",
      spread_mode: "rotate",
      crop_mode: "margins_and_page_numbers",
    }, true);

    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(String(init?.body));
    expect(body.candidate_ids).toEqual(["c-1", "c-2"]);
    expect(body.merge_by_volume).toBe(true);
  });
});
