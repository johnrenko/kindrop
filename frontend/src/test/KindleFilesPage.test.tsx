import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { KindleFilesPage } from "../pages/KindleFilesPage";

const rootListing = {
  path: "/mnt/us",
  root: "/mnt/us",
  parent: null,
  items: [
    {
      name: "documents",
      path: "/mnt/us/documents",
      kind: "directory",
      size_bytes: 0,
      modified_at: 1_756_288_800,
    },
    {
      name: "book.cbz",
      path: "/mnt/us/book.cbz",
      kind: "file",
      size_bytes: 4096,
      modified_at: 1_756_288_700,
    },
  ],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <KindleFilesPage />
    </QueryClientProvider>,
  );
}

describe("Kindle files", () => {
  it("browses Kindle user storage and opens folders", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path.endsWith("%2Fmnt%2Fus%2Fdocuments")) {
        return Response.json({
          path: "/mnt/us/documents",
          root: "/mnt/us",
          parent: "/mnt/us",
          items: [],
        });
      }
      return Response.json(rootListing);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: /Browse your Kindle/ })).toBeInTheDocument();
    expect(await screen.findByText("4.0 KB")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open documents" }));

    expect(await screen.findByText("This folder is empty.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kindle storage" })).toBeInTheDocument();
  });

  it("renames and deletes an item after explicit confirmation", async () => {
    let currentName = "book.cbz";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/kindle/files/rename") {
        currentName = "manga.cbz";
        return Response.json({ path: `/mnt/us/${currentName}` });
      }
      if (path === "/api/kindle/files/delete") return new Response(null, { status: 204 });
      return Response.json({
        ...rootListing,
        items: rootListing.items.map((item) =>
          item.kind === "file"
            ? { ...item, name: currentName, path: `/mnt/us/${currentName}` }
            : item,
        ),
      });
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Rename book.cbz" }));
    const dialog = screen.getByRole("dialog", { name: "Rename item" });
    const input = within(dialog).getByLabelText("New name");
    await userEvent.clear(input);
    await userEvent.type(input, "manga.cbz");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/kindle/files/rename",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/mnt/us/book.cbz", new_name: "manga.cbz" }),
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Delete manga.cbz" }));
    expect(window.confirm).toHaveBeenCalledWith(
      "Delete “manga.cbz” from the Kindle? This cannot be undone.",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/kindle/files/delete",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("moves an item with a navigable destination picker", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/kindle/files/move") {
        return Response.json({ path: "/mnt/us/documents/book.cbz" });
      }
      if (path.endsWith("%2Fmnt%2Fus%2Fdocuments")) {
        return Response.json({
          path: "/mnt/us/documents",
          root: "/mnt/us",
          parent: "/mnt/us",
          items: [],
        });
      }
      return Response.json(rootListing);
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Move book.cbz" }));
    const dialog = screen.getByRole("dialog", { name: "Move item" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Choose documents" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "Move here" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/kindle/files/move",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          path: "/mnt/us/book.cbz",
          destination_directory: "/mnt/us/documents",
        }),
      }),
    );
  });

  it("uploads a manually selected file into the open folder", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/kindle/files/upload") {
        const body = init?.body as FormData;
        expect(body).toBeInstanceOf(FormData);
        expect(body.get("destination_directory")).toBe("/mnt/us");
        expect((body.get("file") as File).name).toBe("manual.cbz");
        expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
        return Response.json({ path: "/mnt/us/manual.cbz" });
      }
      return Response.json(rootListing);
    });
    renderPage();
    await screen.findByRole("button", { name: "Upload file" });
    const file = new File(["comic"], "manual.cbz", { type: "application/vnd.comicbook+zip" });

    await userEvent.upload(screen.getByLabelText("Choose file to upload"), file);

    let dialog = screen.getByRole("dialog", { name: "Upload file" });
    expect(within(dialog).getByText("manual.cbz")).toBeInTheDocument();
    expect(within(dialog).getByText("5 B")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Upload file" })).not.toBeInTheDocument();

    await userEvent.upload(screen.getByLabelText("Choose file to upload"), file);
    dialog = screen.getByRole("dialog", { name: "Upload file" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Upload here" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/kindle/files/upload",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    );
    expect(await screen.findByText("Uploaded manual.cbz to /mnt/us.")).toBeInTheDocument();
  });
});
