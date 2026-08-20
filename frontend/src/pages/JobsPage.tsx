import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Loader2, RotateCcw } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Progress } from "../components/Progress";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";
import { statusLabels } from "../statusLabels";
import type { Job } from "../types";

function jobMatchesStatus(job: Job, status: string) {
  return job.status === status || job.deliveries.some((delivery) => delivery.status === status);
}

export function JobsPage() {
  const client = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const jobs = useQuery({ queryKey: queryKeys.jobs, queryFn: api.jobs, refetchInterval: 5_000 });
  const retry = useMutation({
    mutationFn: api.retryJob,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
  const cancel = useMutation({
    mutationFn: api.cancelJob,
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.jobs }),
        client.invalidateQueries({ queryKey: queryKeys.candidates }),
      ]),
  });
  const resend = useMutation({
    mutationFn: api.resendDelivery,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const job of jobs.data ?? []) {
      const statuses = new Set([job.status, ...job.deliveries.map((delivery) => delivery.status)]);
      for (const status of statuses) counts.set(status, (counts.get(status) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [jobs.data]);
  const visibleJobs = useMemo(() => {
    const all = jobs.data ?? [];
    return statusFilter ? all.filter((job) => jobMatchesStatus(job, statusFilter)) : all;
  }, [jobs.data, statusFilter]);
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
        <>
          {(jobs.data?.length ?? 0) > 0 && (
            <div className="status-filters" role="group" aria-label="Filter jobs by status">
              <button
                className={`filter-chip${statusFilter === null ? " filter-chip--active" : ""}`}
                aria-pressed={statusFilter === null}
                onClick={() => setStatusFilter(null)}
              >
                All <span>{jobs.data?.length}</span>
              </button>
              {statusCounts.map(([status, count]) => (
                <button
                  key={status}
                  className={`filter-chip${statusFilter === status ? " filter-chip--active" : ""}`}
                  aria-pressed={statusFilter === status}
                  onClick={() => setStatusFilter((current) => (current === status ? null : status))}
                >
                  {statusLabels[status] ?? status} <span>{count}</span>
                </button>
              ))}
            </div>
          )}
          {!visibleJobs.length ? (
            <EmptyState eyebrow="No matches" title="Nothing matches this filter">
              <p>No job or delivery currently has this status.</p>
            </EmptyState>
          ) : (
            <ol className="job-ledger">
              {visibleJobs.map((job, index) => (
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
                            {["unknown", "failed"].includes(delivery.status) && (
                              <div className="delivery-resend">
                                {delivery.error_detail && (
                                  delivery.status === "failed" ? (
                                    <p className="notice notice--error"><AlertTriangle size={15} /> {delivery.error_detail}</p>
                                  ) : (
                                    <p className="notice notice--verifying"><Loader2 size={15} className="spin" /> {delivery.error_detail}</p>
                                  )
                                )}
                                <button
                                  className="button button--secondary"
                                  onClick={() => resend.mutate(delivery.id)}
                                  disabled={resend.isPending}
                                >
                                  <RotateCcw size={16} /> Resend as a new job
                                </button>
                              </div>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                    {(["failed", "cancelled"].includes(job.status)) && (
                      <button className="button button--secondary" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}>
                        <RotateCcw size={16} /> Retry as a new job
                      </button>
                    )}
                    {job.status === "queued" && (
                      <button className="button button--secondary" onClick={() => cancel.mutate(job.id)} disabled={cancel.isPending}>
                        <Ban size={16} /> Cancel
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}
