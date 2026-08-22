import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Loader2, RotateCcw } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Progress } from "../components/Progress";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";
import { statusLabels } from "../statusLabels";
import type { ConversionPreset, Job, KindleProfile } from "../types";

function jobMatchesStatus(job: Job, status: string) {
  return job.status === status || job.deliveries.some((delivery) => delivery.status === status);
}

export function JobsPage() {
  const client = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [retryTarget, setRetryTarget] = useState<Job | null>(null);
  const jobs = useQuery({ queryKey: queryKeys.jobs, queryFn: api.jobs, refetchInterval: 5_000 });
  const profiles = useQuery({
    queryKey: queryKeys.profiles,
    queryFn: api.profiles,
    enabled: retryTarget !== null,
  });
  const retry = useMutation({
    mutationFn: ({ id, preset }: { id: string; preset: ConversionPreset }) =>
      api.retryJob(id, preset),
    onSuccess: async () => {
      setRetryTarget(null);
      await client.invalidateQueries({ queryKey: queryKeys.jobs });
    },
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
                                  <RotateCcw size={16} /> Resend with same settings
                                </button>
                              </div>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                    {(["sent", "failed", "cancelled"].includes(job.status)) && (
                      <button
                        className="button button--secondary"
                        onClick={() => {
                          retry.reset();
                          setRetryTarget(job);
                        }}
                      >
                        <RotateCcw size={16} /> Retry with different settings
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
          {retryTarget && (
            <RetryJobDialog
              key={retryTarget.id}
              job={retryTarget}
              profiles={profiles.data ?? []}
              pending={retry.isPending}
              error={retry.error?.message ?? null}
              onCancel={() => setRetryTarget(null)}
              onSubmit={(preset) => retry.mutate({ id: retryTarget.id, preset })}
            />
          )}
        </>
      )}
    </div>
  );
}

function RetryJobDialog({
  job,
  profiles,
  pending,
  error,
  onCancel,
  onSubmit,
}: {
  job: Job;
  profiles: KindleProfile[];
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (preset: ConversionPreset) => void;
}) {
  const [preset, setPreset] = useState<ConversionPreset>({ ...job.preset });
  const availableProfiles = profiles.some((profile) => profile.id === preset.kindle_profile)
    ? profiles
    : [{ id: preset.kindle_profile, name: preset.kindle_profile }, ...profiles];

  return (
    <div className="retry-dialog-backdrop">
      <section
        className="retry-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="retry-dialog-title"
      >
        <span className="eyebrow">Correct a conversion</span>
        <h2 id="retry-dialog-title">Convert and send again</h2>
        <p><strong>{job.title}</strong> will be converted into a new EPUB with these settings.</p>
        {job.status === "sent" && (
          <p className="notice notice--warning">
            The earlier Kindle copy is not removed. This will send another copy after conversion.
          </p>
        )}
        <form
          className="retry-dialog__form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(preset);
          }}
        >
          <label>Kindle profile
            <select
              autoFocus
              value={preset.kindle_profile}
              onChange={(event) => setPreset({ ...preset, kindle_profile: event.target.value })}
            >
              {availableProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.name}</option>
              ))}
            </select>
          </label>
          <label>Reading direction
            <select
              value={preset.reading_direction}
              onChange={(event) => setPreset({ ...preset, reading_direction: event.target.value as ConversionPreset["reading_direction"] })}
            >
              <option value="rtl">Right to left</option>
              <option value="ltr">Left to right</option>
            </select>
          </label>
          <label>Double-page spreads
            <select
              value={preset.spread_mode}
              onChange={(event) => setPreset({ ...preset, spread_mode: event.target.value as ConversionPreset["spread_mode"] })}
            >
              <option value="both">Split + rotate</option>
              <option value="split">Split</option>
              <option value="rotate">Rotate</option>
            </select>
          </label>
          <label>Crop behavior
            <select
              value={preset.crop_mode}
              onChange={(event) => setPreset({ ...preset, crop_mode: event.target.value as ConversionPreset["crop_mode"] })}
            >
              <option value="margins_and_page_numbers">Margins + page numbers</option>
              <option value="margins">Margins only</option>
              <option value="none">Do not crop</option>
            </select>
          </label>
          {error && <p className="form-error">{error}</p>}
          <div className="retry-dialog__actions">
            <button type="button" className="button button--secondary" onClick={onCancel} disabled={pending}>Cancel</button>
            <button type="submit" className="button button--primary" disabled={pending}>
              <RotateCcw size={16} /> {pending ? "Queueing…" : "Queue corrected job"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
