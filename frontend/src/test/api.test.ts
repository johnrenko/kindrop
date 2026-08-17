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
