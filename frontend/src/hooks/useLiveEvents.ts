import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { queryKeys } from "../query";

export function useLiveEvents() {
  const queryClient = useQueryClient();
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = () => undefined;
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.scans });
      void queryClient.invalidateQueries({ queryKey: queryKeys.candidates });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    };
    [
      "scan.started",
      "scan.file_processed",
      "scan.completed",
      "scan.failed",
      "job.started",
      "job.converting",
      "job.sent",
      "job.failed",
      "delivery.sent",
      "delivery.rejected",
    ].forEach((event) => source.addEventListener(event, refresh));
    return () => source.close();
  }, [queryClient]);
}

