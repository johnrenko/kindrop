import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, ChevronRight, Search, Send, Sparkles, X } from "lucide-react";

import { api, formatBytes, type CandidateUpdate } from "../api";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { queryKeys } from "../query";
import type { Candidate, ConversionPreset, MangaMatch } from "../types";

export function ReviewPage() {
  const client = useQueryClient();
  const candidates = useQuery({ queryKey: queryKeys.candidates, queryFn: api.candidates });
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.settings });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [preset, setPreset] = useState<ConversionPreset | null>(null);
  useEffect(() => {
    if (settings.data && !preset) setPreset(settings.data.preset);
  }, [preset, settings.data]);

  const ready = useMemo(
    () => candidates.data?.filter((candidate) => candidate.status === "ready") ?? [],
    [candidates.data],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return ready;
    return ready.filter((candidate) =>
      [candidate.title_override ?? candidate.resolved_title, candidate.name, candidate.path, candidate.metadata.series]
        .some((field) => field?.toLowerCase().includes(needle)),
    );
  }, [query, ready]);
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { title_override?: string | null; status?: string } }) =>
      api.updateCandidate(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.candidates }),
  });
  const launch = useMutation({
    mutationFn: () => api.createBatch([...selected], preset!),
    onSuccess: async () => {
      setSelected(new Set());
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.candidates }),
        client.invalidateQueries({ queryKey: queryKeys.jobs }),
      ]);
    },
  });

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="page review-page">
      <header className="page-header">
        <span className="eyebrow">Candidate review</span>
        <h1>Choose what reaches<br /><em>the next shelf.</em></h1>
        <p className="lead">Metadata comes from ComicInfo.xml when available. Nothing leaves this desk until you confirm the batch.</p>
      </header>

      {!candidates.isLoading && ready.length === 0 ? (
        <EmptyState eyebrow="Review complete" title="No candidates are waiting">
          <p>Run a new Drive scan when another volume is ready.</p>
        </EmptyState>
      ) : (
        <>
          <div className="review-toolbar">
            <label className="check-all">
              <input
                type="checkbox"
                checked={visible.length > 0 && visible.every((item) => selected.has(item.id))}
                onChange={(event) => {
                  setSelected((current) => {
                    const next = new Set(current);
                    for (const item of visible) {
                      if (event.target.checked) next.add(item.id);
                      else next.delete(item.id);
                    }
                    return next;
                  });
                }}
              />
              Select all {visible.length}
            </label>
            <label className="review-search">
              <Search size={15} />
              <input
                type="search"
                placeholder="Filter by title, file or series…"
                aria-label="Filter candidates"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <span>{selected.size} selected</span>
          </div>
          {visible.length === 0 && (
            <EmptyState eyebrow="No matches" title="Nothing matches this filter">
              <p>No candidate matches “{query.trim()}”. Clear the search to see all {ready.length} candidates.</p>
            </EmptyState>
          )}
          <ol className="candidate-list">
            {visible.map((candidate, index) => (
              <li key={candidate.id} className={selected.has(candidate.id) ? "candidate is-selected" : "candidate"}>
                <label className="candidate__check">
                  <input type="checkbox" checked={selected.has(candidate.id)} onChange={() => toggle(candidate.id)} />
                  <span><Check size={15} /></span>
                </label>
                <span className="candidate__number">{String(index + 1).padStart(2, "0")}</span>
                <img
                  className="candidate__preview"
                  src={candidate.metadata.cover_url ?? api.candidatePreviewUrl(candidate.id)}
                  alt=""
                  loading="lazy"
                  onError={(event) => {
                    event.currentTarget.style.visibility = "hidden";
                  }}
                />
                <div className="candidate__body">
                  <div className="candidate__title-row">
                    <div>
                      <input
                        className="title-input"
                        aria-label={`Kindle title for ${candidate.name}`}
                        key={candidate.title_override ?? candidate.resolved_title}
                        defaultValue={candidate.title_override ?? candidate.resolved_title}
                        onBlur={(event) => {
                          const value = event.currentTarget.value.trim();
                          if (value !== (candidate.title_override ?? candidate.resolved_title)) {
                            update.mutate({ id: candidate.id, payload: { title_override: value || null } });
                          }
                        }}
                      />
                      <p>{candidate.path}</p>
                    </div>
                    <StatusBadge status={candidate.status} />
                  </div>
                  <dl className="candidate__meta">
                    <div><dt>Archive</dt><dd>{candidate.name}</dd></div>
                    <div><dt>Size</dt><dd>{formatBytes(candidate.size)}</dd></div>
                    <div><dt>Series</dt><dd>{candidate.metadata.series || "—"}</dd></div>
                    <div><dt>Number</dt><dd>{candidate.metadata.number || "—"}</dd></div>
                    <div><dt>Author</dt><dd>{candidate.metadata.author || "—"}</dd></div>
                  </dl>
                  {expanded === candidate.id && (
                    <CandidateDetails
                      candidate={candidate}
                      onUpdate={(payload) => update.mutate({ id: candidate.id, payload })}
                    />
                  )}
                </div>
                <button
                  className="icon-action"
                  aria-label={`Edit metadata for ${candidate.name}`}
                  aria-expanded={expanded === candidate.id}
                  onClick={() => setExpanded((current) => (current === candidate.id ? null : candidate.id))}
                ><BookOpen size={18} /></button>
                <button
                  className="icon-action"
                  aria-label={`Ignore ${candidate.name}`}
                  onClick={() => update.mutate({ id: candidate.id, payload: { status: "ignored" } })}
                ><X size={18} /></button>
              </li>
            ))}
          </ol>
        </>
      )}

      {preset && ready.length > 0 && (
        <aside className="batch-dock">
          <div>
            <span className="eyebrow">Batch preset</span>
            <strong>{preset.reading_direction === "rtl" ? "Manga · right to left" : "Comics · left to right"}</strong>
          </div>
          <label>Direction
            <select value={preset.reading_direction} onChange={(event) => setPreset({ ...preset, reading_direction: event.target.value as "rtl" | "ltr" })}>
              <option value="rtl">Right to left</option><option value="ltr">Left to right</option>
            </select>
          </label>
          <label>Spreads
            <select value={preset.spread_mode} onChange={(event) => setPreset({ ...preset, spread_mode: event.target.value as ConversionPreset["spread_mode"] })}>
              <option value="both">Split + rotate</option><option value="split">Split</option><option value="rotate">Rotate</option>
            </select>
          </label>
          <button className="button button--primary" disabled={!selected.size || launch.isPending} onClick={() => launch.mutate()}>
            <Send size={17} /> {launch.isPending ? "Creating batch…" : `Convert & send ${selected.size || ""}`} <ChevronRight size={16} />
          </button>
          {launch.error && <p className="form-error">{launch.error.message}</p>}
        </aside>
      )}
    </div>
  );
}

function CandidateDetails({
  candidate,
  onUpdate,
}: {
  candidate: Candidate;
  onUpdate: (payload: CandidateUpdate) => void;
}) {
  const meta = candidate.metadata;
  const [lookup, setLookup] = useState(meta.series ?? candidate.resolved_title);
  const search = useMutation({ mutationFn: (query: string) => api.searchMetadata(query) });

  const applyMatch = (match: MangaMatch) => {
    onUpdate({
      series: match.title,
      author: match.author ?? "",
      cover_url: match.cover_url ?? "",
    });
  };

  const field = (
    label: string,
    key: "series" | "number" | "author",
    placeholder: string,
  ) => (
    <label className="metadata-field">
      {label}
      <input
        key={`${key}-${meta[key] ?? ""}`}
        defaultValue={meta[key] ?? ""}
        placeholder={placeholder}
        onBlur={(event) => {
          const value = event.currentTarget.value.trim();
          if (value !== (meta[key] ?? "")) onUpdate({ [key]: value });
        }}
      />
    </label>
  );

  return (
    <div className="metadata-panel">
      <div className="metadata-fields">
        {field("Series", "series", "Naruto")}
        {field("Number", "number", "3")}
        {field("Author", "author", "Masashi Kishimoto")}
      </div>
      <div className="metadata-cover">
        <span className="eyebrow">Cover</span>
        {meta.cover_url ? (
          <>
            <img src={meta.cover_url} alt={`Cover for ${meta.series ?? candidate.resolved_title}`} />
            <button className="button" onClick={() => onUpdate({ cover_url: "" })}>
              Use first page instead
            </button>
          </>
        ) : (
          <p>The first page of the archive is used as the cover.</p>
        )}
      </div>
      <form
        className="metadata-lookup"
        onSubmit={(event) => {
          event.preventDefault();
          if (lookup.trim()) search.mutate(lookup.trim());
        }}
      >
        <label className="review-search">
          <Sparkles size={15} />
          <input
            type="search"
            aria-label="Search AniList"
            placeholder="Search AniList…"
            value={lookup}
            onChange={(event) => setLookup(event.target.value)}
          />
        </label>
        <button className="button" type="submit" disabled={search.isPending}>
          {search.isPending ? "Searching…" : "Search AniList"}
        </button>
      </form>
      {search.error && <p className="form-error">{search.error.message}</p>}
      {search.data && search.data.length === 0 && <p>AniList found no matching manga.</p>}
      {search.data && search.data.length > 0 && (
        <ul className="anilist-results">
          {search.data.map((match) => (
            <li key={match.anilist_id}>
              <button type="button" onClick={() => applyMatch(match)}>
                {match.cover_url ? <img src={match.cover_url} alt="" loading="lazy" /> : <span className="anilist-nocover" />}
                <strong>{match.title}</strong>
                <span>
                  {[match.author, match.format, match.year].filter(Boolean).join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

