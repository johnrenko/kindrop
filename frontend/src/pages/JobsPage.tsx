import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Progress } from "../components/Progress";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";

export function JobsPage() {
  const client = useQueryClient();
  const jobs = useQuery({ queryKey: queryKeys.jobs, queryFn: api.jobs, refetchInterval: 5_000 });
  const retry = useMutation({
    mutationFn: api.retryJob,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
  return (
    <div className="page jobs-page">
      <header className="page-header page-header--compact">
        <span className="eyebrow">Jobs & history</span>
        <h1>Every volume leaves<br /><em>a paper trail.</em></h1>
      </header>
      {!jobs.isLoading && !jobs.data?.length ? (
        <EmptyState eyebrow="No history yet" title="Your first dispatch will appear here">
          <p>Each conversion, EPUB part, Gmail message and Amazon response is kept together.</p>
        </EmptyState>
      ) : (
        <ol className="job-ledger">
          {jobs.data?.map((job, index) => (
            <li key={job.id} className="job-entry">
              <div className="job-entry__index">{String(index + 1).padStart(3, "0")}</div>
              <div className="job-entry__main">
                <div className="job-entry__heading">
                  <div><h2>{job.title}</h2><p>{new Date(job.created_at).toLocaleString()}{job.merged_count && job.merged_count > 1 ? ` · ${job.merged_count} chapters` : ""}</p></div>
                  <StatusBadge status={job.status} />
                </div>
                {!["sent", "failed", "cancelled"].includes(job.status) && <Progress value={job.progress} label={job.status} />}
                {job.error && <p className="notice notice--error"><AlertTriangle size={17} /> {job.error}</p>}
                {job.deliveries.length > 0 && (
                  <ol className="delivery-list">
                    {job.deliveries.map((delivery) => (
                      <li key={delivery.id}>
                        <div><strong>{delivery.filename}</strong><small>Part {delivery.part_number}/{delivery.total_parts}</small></div>
                        <StatusBadge status={delivery.status} />
                        {delivery.error_code && <span className="error-code">{delivery.error_code}</span>}
                      </li>
                    ))}
                  </ol>
                )}
                {(["failed", "cancelled"].includes(job.status)) && (
                  <button className="button button--secondary" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}>
                    <RotateCcw size={16} /> Retry as a new job
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
