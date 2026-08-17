import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true },
    mutations: { retry: 0 },
  },
});

export const queryKeys = {
  setup: ["setup"] as const,
  settings: ["settings"] as const,
  profiles: ["profiles"] as const,
  scans: ["scans"] as const,
  candidates: ["candidates"] as const,
  jobs: ["jobs"] as const,
};

