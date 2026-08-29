import type {
  Candidate,
  CandidateSeries,
  CandidateOption,
  ConversionPreset,
  DriveFolder,
  Job,
  KindleStorageListing,
  KindleProfile,
  MangaMatch,
  Scan,
  Settings,
  SetupStatus,
  SshStatus,
  SshHostKey,
} from "./types";

export interface CandidateUpdate {
  title_override?: string | null;
  status?: string;
  series?: string;
  number?: string;
  author?: string;
  cover_url?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = init?.body instanceof FormData
    ? init.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const response = await fetch(path, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Kindrop returned ${response.status}`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  setup: () => request<SetupStatus>("/api/setup/status"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (settings: Settings) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  sshStatus: () => request<SshStatus>("/api/ssh/status"),
  testSsh: () => request<SshStatus>("/api/ssh/test", { method: "POST" }),
  inspectSshHostKey: () => request<SshHostKey>("/api/ssh/host-key", { method: "POST" }),
  trustSshHostKey: (fingerprint: string) =>
    request<SshHostKey>("/api/ssh/trust", {
      method: "POST",
      body: JSON.stringify({ fingerprint }),
    }),
  kindleFiles: (path = "/mnt/us") =>
    request<KindleStorageListing>(`/api/kindle/files?path=${encodeURIComponent(path)}`),
  uploadKindleItem: (file: File, destinationDirectory: string) => {
    const body = new FormData();
    body.append("destination_directory", destinationDirectory);
    body.append("file", file);
    return request<{ path: string }>("/api/kindle/files/upload", {
      method: "POST",
      body,
    });
  },
  renameKindleItem: (path: string, newName: string) =>
    request<{ path: string }>("/api/kindle/files/rename", {
      method: "POST",
      body: JSON.stringify({ path, new_name: newName }),
    }),
  moveKindleItem: (path: string, destinationDirectory: string) =>
    request<{ path: string }>("/api/kindle/files/move", {
      method: "POST",
      body: JSON.stringify({ path, destination_directory: destinationDirectory }),
    }),
  deleteKindleItem: (path: string) =>
    request<void>("/api/kindle/files/delete", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  bulkDeleteKindleItems: (paths: string[]) =>
    request<void>("/api/kindle/files/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ paths }),
    }),
  profiles: () => request<KindleProfile[]>("/api/kindle-profiles"),
  scans: () => request<Scan[]>("/api/scans"),
  startScan: () => request<Scan>("/api/scans", { method: "POST" }),
  cancelScan: (id: string) => request(`/api/scans/${id}/cancel`, { method: "POST" }),
  pauseScan: (id: string) => request(`/api/scans/${id}/pause`, { method: "POST" }),
  resumeScan: (id: string) => request(`/api/scans/${id}/resume`, { method: "POST" }),
  candidates: () => request<Candidate[]>("/api/candidates"),
  candidateSeries: () => request<CandidateSeries[]>("/api/candidate-series"),
  applyCandidateSeries: (
    groupId: string,
    candidateIds: string[],
    series: string,
    metadata: { author?: string | null; cover_url?: string | null } = {},
  ) =>
    request<Candidate[]>("/api/candidate-series", {
      method: "PATCH",
      body: JSON.stringify({ group_id: groupId, candidate_ids: candidateIds, series, ...metadata }),
    }),
  updateCandidate: (id: string, update: CandidateUpdate) =>
    request<Candidate>(`/api/candidates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(update),
    }),
  candidatePreviewUrl: (id: string) => `/api/candidates/${encodeURIComponent(id)}/preview`,
  searchMetadata: (query: string) =>
    request<MangaMatch[]>(`/api/metadata/search?query=${encodeURIComponent(query)}`),
  createBatch: (
    candidateIds: string[],
    preset: ConversionPreset,
    mergeByVolume = false,
    candidateOptions: CandidateOption[] = [],
  ) =>
    request<{ id: string }>("/api/batches", {
      method: "POST",
      body: JSON.stringify({
        candidate_ids: candidateIds,
        preset,
        merge_by_volume: mergeByVolume,
        candidate_options: candidateOptions,
      }),
    }),
  jobs: () => request<Job[]>("/api/jobs"),
  retryJob: (id: string, preset: ConversionPreset) =>
    request(`/api/jobs/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ preset }),
    }),
  cancelJob: (id: string) => request(`/api/jobs/${id}/cancel`, { method: "POST" }),
  resendDelivery: (id: string) =>
    request(`/api/deliveries/${id}/resend`, { method: "POST" }),
  sendDeliveryByEmail: (id: string) =>
    request(`/api/deliveries/${id}/email-fallback`, { method: "POST" }),
  uploadGoogleClient: (credentials: unknown) =>
    request<void>("/api/oauth/client", {
      method: "POST",
      body: JSON.stringify({ credentials }),
    }),
  oauthStart: () => request<{ authorization_url: string }>("/api/oauth/start"),
  disconnectGoogle: () => request<void>("/api/oauth", { method: "DELETE" }),
  folders: (parentId: string) =>
    request<{ folders: DriveFolder[]; next_page_token: string | null }>(
      `/api/drive/folders?parent_id=${encodeURIComponent(parentId)}`,
    ),
  purgeCache: () => request<void>("/api/cache", { method: "DELETE" }),
  clearHistory: () => request<void>("/api/history", { method: "DELETE" }),
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (const candidate of units) {
    unit = candidate;
    if (value < 1024 || candidate === "TB") break;
    value /= 1024;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`;
}
