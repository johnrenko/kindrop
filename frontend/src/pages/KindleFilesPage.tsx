import { FormEvent, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronRight,
  File,
  Folder,
  FolderInput,
  HardDrive,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import { api, formatBytes } from "../api";
import { queryKeys } from "../query";
import type { KindleStorageItem, KindleStorageListing } from "../types";

const KINDLE_STORAGE_ROOT = "/mnt/us";

function parentPath(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash <= KINDLE_STORAGE_ROOT.length ? KINDLE_STORAGE_ROOT : path.slice(0, slash);
}

function breadcrumbs(listing: KindleStorageListing | undefined, currentPath: string) {
  const root = listing?.root ?? KINDLE_STORAGE_ROOT;
  const path = listing?.path ?? currentPath;
  const parts = path.slice(root.length).split("/").filter(Boolean);
  return [
    { name: "Kindle storage", path: root },
    ...parts.map((name, index) => ({
      name,
      path: `${root}/${parts.slice(0, index + 1).join("/")}`,
    })),
  ];
}

function isSameOrDescendant(source: string, candidate: string): boolean {
  return candidate === source || candidate.startsWith(`${source}/`);
}

function formatModified(timestamp: number | null): string {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(timestamp * 1000));
}

export function KindleFilesPage() {
  const client = useQueryClient();
  const [path, setPath] = useState(KINDLE_STORAGE_ROOT);
  const [renameTarget, setRenameTarget] = useState<KindleStorageItem | null>(null);
  const [renameName, setRenameName] = useState("");
  const [moveTarget, setMoveTarget] = useState<KindleStorageItem | null>(null);
  const [movePath, setMovePath] = useState(KINDLE_STORAGE_ROOT);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null);
  const selectionAnchor = useRef<string | null>(null);
  const clearSelection = () => {
    selectionAnchor.current = null;
    setSelectedPaths([]);
  };
  const clearUploadSelection = () => {
    setUploadFile(null);
    if (uploadInput.current) uploadInput.current.value = "";
  };

  const listing = useQuery({
    queryKey: [...queryKeys.kindleFiles, path],
    queryFn: () => api.kindleFiles(path),
  });
  const moveListing = useQuery({
    queryKey: [...queryKeys.kindleFiles, "destination", movePath],
    queryFn: () => api.kindleFiles(movePath),
    enabled: Boolean(moveTarget),
  });
  const refreshFiles = () => client.invalidateQueries({ queryKey: queryKeys.kindleFiles });
  useEffect(() => {
    if (!listing.data) return;
    const availablePaths = new Set(listing.data.items.map((item) => item.path));
    if (selectionAnchor.current && !availablePaths.has(selectionAnchor.current)) {
      selectionAnchor.current = null;
    }
    setSelectedPaths((current) => {
      const availableSelection = current.filter((itemPath) => availablePaths.has(itemPath));
      return availableSelection.length === current.length ? current : availableSelection;
    });
  }, [listing.data]);
  const rename = useMutation({
    mutationFn: ({ target, name }: { target: string; name: string }) =>
      api.renameKindleItem(target, name),
    onSuccess: async () => {
      setRenameTarget(null);
      await refreshFiles();
    },
  });
  const move = useMutation({
    mutationFn: ({ target, destination }: { target: string; destination: string }) =>
      api.moveKindleItem(target, destination),
    onSuccess: async () => {
      setMoveTarget(null);
      await refreshFiles();
    },
  });
  const deleteItemMutation = useMutation({
    mutationFn: api.deleteKindleItem,
    onSuccess: async (_result, removedPath) => {
      if (selectionAnchor.current === removedPath) selectionAnchor.current = null;
      setSelectedPaths((current) => current.filter((itemPath) => itemPath !== removedPath));
      await refreshFiles();
    },
  });
  const bulkDeleteMutation = useMutation({
    mutationFn: api.bulkDeleteKindleItems,
    onSuccess: async (_result, removedPaths) => {
      clearSelection();
      setDeleteSuccess(
        `${removedPaths.length} ${removedPaths.length === 1 ? "item" : "items"} deleted from the Kindle.`,
      );
      await refreshFiles();
    },
  });
  const upload = useMutation({
    mutationFn: ({ file, destination }: { file: File; destination: string }) =>
      api.uploadKindleItem(file, destination),
    onSuccess: async (_result, variables) => {
      clearUploadSelection();
      setUploadSuccess(`Uploaded ${variables.file.name} to ${variables.destination}.`);
      await refreshFiles();
    },
  });

  const openRename = (item: KindleStorageItem) => {
    rename.reset();
    setRenameName(item.name);
    setRenameTarget(item);
  };
  const openMove = (item: KindleStorageItem) => {
    move.reset();
    setMovePath(KINDLE_STORAGE_ROOT);
    setMoveTarget(item);
  };
  const deleteItem = (item: KindleStorageItem) => {
    if (!window.confirm(`Delete “${item.name}” from the Kindle? This cannot be undone.`)) return;
    setDeleteSuccess(null);
    deleteItemMutation.mutate(item.path);
  };
  const openPath = (nextPath: string) => {
    clearSelection();
    setDeleteSuccess(null);
    setPath(nextPath);
  };
  const toggleSelection = (itemPath: string, checked: boolean, extendRange: boolean) => {
    bulkDeleteMutation.reset();
    setDeleteSuccess(null);
    const anchorPath = selectionAnchor.current;
    setSelectedPaths((current) => {
      const selected = new Set(current);
      let affectedPaths = [itemPath];
      if (extendRange && anchorPath && listing.data) {
        const anchorIndex = listing.data.items.findIndex((item) => item.path === anchorPath);
        const itemIndex = listing.data.items.findIndex((item) => item.path === itemPath);
        if (anchorIndex >= 0 && itemIndex >= 0) {
          const start = Math.min(anchorIndex, itemIndex);
          const end = Math.max(anchorIndex, itemIndex);
          affectedPaths = listing.data.items.slice(start, end + 1).map((item) => item.path);
        }
      }
      for (const affectedPath of affectedPaths) {
        if (checked) selected.add(affectedPath);
        else selected.delete(affectedPath);
      }
      return listing.data
        ? listing.data.items.map((item) => item.path).filter((path) => selected.has(path))
        : [...selected];
    });
    selectionAnchor.current = itemPath;
  };
  const allItemsSelected = Boolean(
    listing.data?.items.length
    && listing.data.items.every((item) => selectedPaths.includes(item.path)),
  );
  const toggleAllItems = () => {
    bulkDeleteMutation.reset();
    setDeleteSuccess(null);
    selectionAnchor.current = null;
    setSelectedPaths(
      allItemsSelected ? [] : (listing.data?.items.map((item) => item.path) ?? []),
    );
  };
  const deleteSelected = () => {
    const count = selectedPaths.length;
    if (!count) return;
    const noun = count === 1 ? "item" : "items";
    if (!window.confirm(`Delete ${count} selected ${noun} from the Kindle? This cannot be undone.`)) return;
    setDeleteSuccess(null);
    bulkDeleteMutation.mutate(selectedPaths);
  };

  return (
    <div className="page kindle-files-page">
      <header className="page-header page-header--split kindle-files-header">
        <div>
          <span className="eyebrow">Kindle storage · SSH</span>
          <h1>Browse your<br /><em>Kindle.</em></h1>
          <p className="lead">Organize files and folders directly in the Kindle’s user storage. System files stay outside this browser.</p>
        </div>
        <div className="header-action">
          <span className="label">Live path</span>
          <strong>{listing.data?.path ?? path}</strong>
          <button className="button button--secondary" type="button" onClick={() => listing.refetch()} disabled={listing.isFetching}>
            <RefreshCw size={17} className={listing.isFetching ? "spin" : undefined} />
            {listing.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <section className="kindle-browser" aria-label="Kindle file browser">
        <div className="kindle-browser__toolbar">
          <button
            className="icon-action"
            type="button"
            aria-label="Go to parent folder"
            disabled={!listing.data?.parent}
            onClick={() => listing.data?.parent && openPath(listing.data.parent)}
          >
            <ArrowLeft size={19} />
          </button>
          <nav className="kindle-breadcrumbs" aria-label="Kindle path">
            {breadcrumbs(listing.data, path).map((crumb, index, all) => (
              <span key={crumb.path}>
                {index > 0 && <ChevronRight size={14} aria-hidden="true" />}
                <button type="button" onClick={() => openPath(crumb.path)} aria-current={index === all.length - 1 ? "page" : undefined}>
                  {index === 0 && <HardDrive size={15} aria-hidden="true" />}
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>
          <div className="kindle-browser__toolbar-actions">
            <span className="kindle-browser__count">{listing.data?.items.length ?? 0} items</span>
            <button
              className="button button--primary kindle-browser__upload"
              type="button"
              onClick={() => uploadInput.current?.click()}
              disabled={upload.isPending}
            >
              <Upload size={16} /> Upload file
            </button>
            <input
              ref={uploadInput}
              className="visually-hidden"
              type="file"
              aria-label="Choose file to upload"
              onChange={(event) => {
                upload.reset();
                setUploadSuccess(null);
                setUploadFile(event.target.files?.[0] ?? null);
              }}
            />
          </div>
        </div>

        {selectedPaths.length > 0 && (
          <div className="kindle-selection-bar">
            <strong>{selectedPaths.length} selected</strong>
            <div>
              <button className="button button--secondary" type="button" onClick={clearSelection} disabled={bulkDeleteMutation.isPending}>
                Clear selection
              </button>
              <button className="button kindle-selection-bar__delete" type="button" onClick={deleteSelected} disabled={bulkDeleteMutation.isPending}>
                <Trash2 size={16} /> {bulkDeleteMutation.isPending ? "Deleting…" : "Delete selected"}
              </button>
            </div>
          </div>
        )}

        {listing.isLoading && <div className="kindle-browser__state">Reading Kindle storage…</div>}
        {listing.error && (
          <div className="kindle-browser__state kindle-browser__state--error">
            <HardDrive size={28} />
            <div><strong>Kindle unavailable</strong><p>{listing.error.message}</p></div>
          </div>
        )}
        {listing.data && listing.data.items.length === 0 && (
          <div className="kindle-browser__state"><Folder size={28} /><strong>This folder is empty.</strong></div>
        )}
        {listing.data && listing.data.items.length > 0 && (
          <div className="kindle-file-table">
            <div className="kindle-file-table__head">
              <label className="kindle-file-select">
                <input type="checkbox" aria-label="Select all items" checked={allItemsSelected} onChange={toggleAllItems} disabled={bulkDeleteMutation.isPending} />
              </label>
              <span>Name</span><span>Size</span><span>Modified</span><span>Actions</span>
            </div>
            <ul>
              {listing.data.items.map((item) => (
                <li key={item.path} className={`kindle-file-row${selectedPaths.includes(item.path) ? " kindle-file-row--selected" : ""}`}>
                  <label className="kindle-file-select">
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.name}`}
                      checked={selectedPaths.includes(item.path)}
                      onChange={(event) => toggleSelection(
                        item.path,
                        event.currentTarget.checked,
                        (event.nativeEvent as MouseEvent).shiftKey,
                      )}
                      disabled={bulkDeleteMutation.isPending}
                    />
                  </label>
                  <div className="kindle-file-row__name">
                    {item.kind === "directory" ? <Folder size={20} /> : <File size={20} />}
                    {item.kind === "directory" ? (
                      <button type="button" onClick={() => openPath(item.path)} aria-label={`Open ${item.name}`}>{item.name}</button>
                    ) : <strong>{item.name}</strong>}
                    <small>{item.kind}</small>
                  </div>
                  <span>{item.kind === "file" ? formatBytes(item.size_bytes) : "—"}</span>
                  <time dateTime={item.modified_at ? new Date(item.modified_at * 1000).toISOString() : undefined}>{formatModified(item.modified_at)}</time>
                  <div className="kindle-file-row__actions">
                    <button type="button" aria-label={`Rename ${item.name}`} title="Rename" onClick={() => openRename(item)} disabled={bulkDeleteMutation.isPending}><Pencil size={17} /></button>
                    <button type="button" aria-label={`Move ${item.name}`} title="Move" onClick={() => openMove(item)} disabled={bulkDeleteMutation.isPending}><FolderInput size={17} /></button>
                    <button type="button" aria-label={`Delete ${item.name}`} title="Delete" onClick={() => deleteItem(item)} disabled={deleteItemMutation.isPending || bulkDeleteMutation.isPending}><Trash2 size={17} /></button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
        {(deleteItemMutation.error || bulkDeleteMutation.error) && <p className="form-error kindle-browser__error">{(deleteItemMutation.error ?? bulkDeleteMutation.error)?.message}</p>}
        {deleteSuccess && <p className="kindle-browser__success" role="status">{deleteSuccess}</p>}
        {uploadSuccess && <p className="kindle-browser__success" role="status">{uploadSuccess}</p>}
      </section>

      {uploadFile && (
        <div className="file-dialog-backdrop">
          <section className="file-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title">
            <button className="file-dialog__close" type="button" aria-label="Close upload dialog" onClick={clearUploadSelection}><X size={19} /></button>
            <span className="eyebrow">Manual SSH upload</span>
            <h2 id="upload-title">Upload file</h2>
            <div className="upload-summary">
              <File size={22} />
              <div><strong>{uploadFile.name}</strong><small>{formatBytes(uploadFile.size)}</small></div>
            </div>
            <p>The file will be uploaded to <strong>{listing.data?.path ?? path}</strong>. Existing items are never overwritten.</p>
            {upload.error && <p className="form-error">{upload.error.message}</p>}
            <div className="file-dialog__actions">
              <button className="button button--secondary" type="button" onClick={clearUploadSelection}>Cancel</button>
              <button
                className="button button--primary"
                type="button"
                disabled={upload.isPending}
                onClick={() => upload.mutate({ file: uploadFile, destination: listing.data?.path ?? path })}
              >
                {upload.isPending ? "Uploading…" : "Upload here"}
              </button>
            </div>
          </section>
        </div>
      )}

      {renameTarget && (
        <div className="file-dialog-backdrop">
          <section className="file-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-title">
            <button className="file-dialog__close" type="button" aria-label="Close rename dialog" onClick={() => setRenameTarget(null)}><X size={19} /></button>
            <span className="eyebrow">Kindle storage</span>
            <h2 id="rename-title">Rename item</h2>
            <p>Choose a new name for <strong>{renameTarget.name}</strong>.</p>
            <form onSubmit={(event: FormEvent) => { event.preventDefault(); rename.mutate({ target: renameTarget.path, name: renameName }); }}>
              <label>New name<input autoFocus required value={renameName} onChange={(event) => setRenameName(event.target.value)} /></label>
              {rename.error && <p className="form-error">{rename.error.message}</p>}
              <div className="file-dialog__actions">
                <button className="button button--secondary" type="button" onClick={() => setRenameTarget(null)}>Cancel</button>
                <button className="button button--primary" disabled={rename.isPending || !renameName}>{rename.isPending ? "Renaming…" : "Rename"}</button>
              </div>
            </form>
          </section>
        </div>
      )}

      {moveTarget && (
        <div className="file-dialog-backdrop">
          <section className="file-dialog file-dialog--move" role="dialog" aria-modal="true" aria-labelledby="move-title">
            <button className="file-dialog__close" type="button" aria-label="Close move dialog" onClick={() => setMoveTarget(null)}><X size={19} /></button>
            <span className="eyebrow">Choose a destination</span>
            <h2 id="move-title">Move item</h2>
            <p>Move <strong>{moveTarget.name}</strong> into:</p>
            <div className="move-picker__path"><HardDrive size={16} /><span>{movePath}</span></div>
            <div className="move-picker">
              {moveListing.data?.parent && (
                <button type="button" onClick={() => setMovePath(moveListing.data!.parent!)}><ArrowLeft size={17} /> Parent folder</button>
              )}
              {moveListing.data?.items.filter((item) => item.kind === "directory").map((item) => {
                const insideSource = isSameOrDescendant(moveTarget.path, item.path);
                return (
                  <button key={item.path} type="button" disabled={insideSource} onClick={() => setMovePath(item.path)} aria-label={`Choose ${item.name}`}>
                    <Folder size={18} /> <span>{item.name}</span><ChevronRight size={15} />
                  </button>
                );
              })}
              {moveListing.isLoading && <span className="move-picker__state">Reading folders…</span>}
              {moveListing.error && <span className="move-picker__state form-error">{moveListing.error.message}</span>}
            </div>
            {move.error && <p className="form-error">{move.error.message}</p>}
            <div className="file-dialog__actions">
              <button className="button button--secondary" type="button" onClick={() => setMoveTarget(null)}>Cancel</button>
              <button
                className="button button--primary"
                type="button"
                disabled={move.isPending || movePath === parentPath(moveTarget.path) || isSameOrDescendant(moveTarget.path, movePath)}
                onClick={() => move.mutate({ target: moveTarget.path, destination: movePath })}
              >
                {move.isPending ? "Moving…" : "Move here"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
