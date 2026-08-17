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
