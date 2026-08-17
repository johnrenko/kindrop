import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight, FolderSearch, Pause, Play, RefreshCw, Square } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Progress } from "../components/Progress";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";

export function DashboardPage() {
  const client = useQueryClient();
  const setup = useQuery({ queryKey: queryKeys.setup, queryFn: api.setup });
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.settings });
  const scans = useQuery({ queryKey: queryKeys.scans, queryFn: api.scans, refetchInterval: 5_000 });
  const jobs = useQuery({ queryKey: queryKeys.jobs, queryFn: api.jobs, refetchInterval: 5_000 });
  const candidates = useQuery({ queryKey: queryKeys.candidates, queryFn: api.candidates });
  const startScan = useMutation({
    mutationFn: api.startScan,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.scans });
    },
  });
  const refreshScans = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.scans });
  };
  const pauseScan = useMutation({ mutationFn: api.pauseScan, onSuccess: refreshScans });
  const resumeScan = useMutation({ mutationFn: api.resumeScan, onSuccess: refreshScans });
  const stopScan = useMutation({ mutationFn: api.cancelScan, onSuccess: refreshScans });

  const latestScan = scans.data?.[0];
  const active = latestScan && ["queued", "scanning"].includes(latestScan.status);
  const paused = latestScan?.status === "paused";
  const readyCount = candidates.data?.filter((candidate) => candidate.status === "ready").length ?? 0;
  const recentJobs = jobs.data?.slice(0, 4) ?? [];

  return (
    <div className="page dashboard-page">
      <header className="page-header page-header--split">
        <div>
          <span className="eyebrow">Personal edition · Localhost</span>
          <h1>Your reading<br /><em>dispatch desk.</em></h1>
        </div>
        <div className="header-action">
          <span className="label">Source folder</span>
          <strong>{settings.data?.source_folder_name ?? "Not selected"}</strong>
          <button
            className="button button--primary"
            onClick={() => startScan.mutate()}
            disabled={!setup.data?.ready || Boolean(active) || paused || startScan.isPending}
          >
            <FolderSearch size={18} aria-hidden="true" />
            {active ? "Scanning Drive…" : paused ? "Scan paused" : "Scan source folder"}
          </button>
          {startScan.error && <p className="form-error">{startScan.error.message}</p>}
        </div>
      </header>

      {!setup.isLoading && !setup.data?.ready && (
        <section className="setup-callout">
          <span className="setup-callout__index">Before volume 01</span>
          <div>
            <h2>Finish the shelf setup</h2>
            <p>Connect Google, choose a My Drive folder, then add your Kindle destination.</p>
          </div>
          <Link to="/settings" className="text-link">Open settings <ArrowRight size={16} /></Link>
        </section>
      )}

      <section className="workbench" aria-label="Current work">
        <div className="workbench__primary">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Current intake</span>
              <h2>{latestScan ? "Latest scan" : "The desk is clear"}</h2>
            </div>
            {latestScan && <StatusBadge status={latestScan.status} />}
          </div>
          {latestScan ? (
            <div className="scan-sheet">
              <Progress
                value={latestScan.progress}
                label={active ? "Inspecting archives" : paused ? "Paused" : "Scan progress"}
              />
              {(active || paused) && (
                <div className="scan-actions">
                  {paused ? (
                    <button
                      className="button button--secondary"
                      onClick={() => resumeScan.mutate(latestScan.id)}
                      disabled={resumeScan.isPending}
                    >
                      <Play size={16} aria-hidden="true" />
                      Resume scan
                    </button>
                  ) : (
                    <button
                      className="button button--secondary"
                      onClick={() => pauseScan.mutate(latestScan.id)}
                      disabled={pauseScan.isPending}
                    >
                      <Pause size={16} aria-hidden="true" />
                      Pause scan
                    </button>
                  )}
                  <button
                    className="button button--danger"
                    onClick={() => stopScan.mutate(latestScan.id)}
                    disabled={stopScan.isPending}
                  >
                    <Square size={16} aria-hidden="true" />
                    Stop scan
                  </button>
                  {(pauseScan.error || resumeScan.error || stopScan.error) && (
                    <p className="form-error">
                      {(pauseScan.error ?? resumeScan.error ?? stopScan.error)?.message}
                    </p>
                  )}
                </div>
              )}
              <dl className="scan-facts">
                <div><dt>New revisions</dt><dd>{latestScan.discovered_count}</dd></div>
                <div><dt>Inspected</dt><dd>{latestScan.processed_count}</dd></div>
                <div><dt>Ready to review</dt><dd>{readyCount}</dd></div>
              </dl>
              {latestScan.error && <p className="notice notice--error">{latestScan.error}</p>}
              {readyCount > 0 && (
                <Link to="/review" className="button button--ink">Review {readyCount} candidates <ArrowRight size={17} /></Link>
              )}
            </div>
          ) : (
            <EmptyState eyebrow="No scan yet" title="Bring in your first volume">
              <p>A scan reads new CBR and CBZ revisions without changing anything in Drive.</p>
            </EmptyState>
          )}
        </div>

        <aside className="workbench__margin">
          <span className="margin-number">{String(recentJobs.length).padStart(2, "0")}</span>
          <h2>Recent dispatches</h2>
          {recentJobs.length ? (
            <ol className="dispatch-list">
              {recentJobs.map((job) => (
                <li key={job.id}>
                  <div><strong>{job.title}</strong><small>{new Date(job.created_at).toLocaleDateString()}</small></div>
                  <StatusBadge status={job.status} />
                </li>
              ))}
            </ol>
          ) : (
            <p className="quiet-copy">Converted volumes will collect here, with every Kindle delivery accounted for.</p>
          )}
          <Link to="/jobs" className="text-link">View full history <ArrowRight size={16} /></Link>
        </aside>
      </section>

      <footer className="desk-footer">
        <RefreshCw size={15} aria-hidden="true" />
        Kindrop checks Amazon responses while this app is running.
      </footer>
    </div>
  );
}

