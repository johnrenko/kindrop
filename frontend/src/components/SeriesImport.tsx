import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, LibraryBig, Search, Sparkles } from "lucide-react";

import { api } from "../api";
import { queryKeys } from "../query";
import type { CandidateSeries, MangaMatch } from "../types";

export function SeriesImport() {
  const suggestions = useQuery({
    queryKey: queryKeys.candidateSeries,
    queryFn: api.candidateSeries,
  });
  if (!suggestions.data?.length) return null;

  return (
    <section className="series-import" aria-labelledby="series-import-title">
      <div className="series-import__heading">
        <span className="series-import__mark"><LibraryBig size={22} aria-hidden="true" /></span>
        <div>
          <span className="eyebrow">Library intelligence</span>
          <h2 id="series-import-title">Smart series import</h2>
          <p>Match once, then apply the series, author, cover and inferred volume numbers to every ready Candidate.</p>
        </div>
      </div>
      <ol className="series-clusters">
        {suggestions.data.map((suggestion) => (
          <SeriesCluster key={suggestion.id} suggestion={suggestion} />
        ))}
      </ol>
    </section>
  );
}

function SeriesCluster({ suggestion }: { suggestion: CandidateSeries }) {
  const client = useQueryClient();
  const [matching, setMatching] = useState(false);
  const [lookup, setLookup] = useState(suggestion.suggested_series ?? "");
  const search = useMutation({ mutationFn: api.searchMetadata });
  const apply = useMutation({
    mutationFn: ({
      series,
      author,
      cover_url,
    }: {
      series: string;
      author?: string | null;
      cover_url?: string | null;
    }) =>
      api.applyCandidateSeries(
        suggestion.id,
        suggestion.members.map((member) => member.candidate_id),
        series,
        { author, cover_url },
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.candidates }),
        client.invalidateQueries({ queryKey: queryKeys.candidateSeries }),
      ]);
    },
  });
  const label = suggestion.suggested_series ?? "Detected series";
  const range = suggestion.first_volume === suggestion.last_volume
    ? `Vol. ${suggestion.first_volume}`
    : `Vol. ${suggestion.first_volume}–${suggestion.last_volume}`;

  const applyMatch = (match: MangaMatch) => apply.mutate({
    series: match.title,
    author: match.author,
    cover_url: match.cover_url,
  });

  return (
    <li className="series-cluster">
      <span className="series-cluster__range">{range}</span>
      <div className="series-cluster__body">
        <h3>{suggestion.suggested_series ?? "Name this series"}</h3>
        <p>
          <strong>{suggestion.ready_count} ready</strong>
          <span>{suggestion.known_count} known across current work and history</span>
        </p>
        {(suggestion.missing_volumes.length > 0 || suggestion.duplicate_volumes.length > 0) && (
          <div className="series-cluster__warning">
            <AlertTriangle size={16} aria-hidden="true" />
            <span>
              {suggestion.missing_volumes.length > 0 && `Missing ${suggestion.missing_volumes.join(", ")}. `}
              {suggestion.duplicate_volumes.length > 0 && `Duplicates ${suggestion.duplicate_volumes.join(", ")}.`}
            </span>
          </div>
        )}
      </div>
      <div className="series-cluster__actions">
        {suggestion.suggested_series && (
          <button
            className="button button--secondary"
            disabled={apply.isPending}
            onClick={() => apply.mutate({
              series: suggestion.suggested_series!,
            })}
          >
            <Check size={16} aria-hidden="true" /> Use detected name
          </button>
        )}
        <button
          className="button button--ink"
          aria-label={`Match metadata for ${label}`}
          aria-expanded={matching}
          onClick={() => setMatching((current) => !current)}
        >
          <Sparkles size={16} aria-hidden="true" /> Match AniList
        </button>
      </div>
      {matching && (
        <div className="series-match">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (lookup.trim()) search.mutate(lookup.trim());
            }}
          >
            <label>
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                aria-label={`Search metadata for ${label}`}
                placeholder="Series title…"
                value={lookup}
                onChange={(event) => setLookup(event.target.value)}
              />
            </label>
            <button className="button button--secondary" disabled={!lookup.trim() || search.isPending}>
              {search.isPending ? "Searching…" : "Search AniList"}
            </button>
          </form>
          {search.error && <p className="form-error">{search.error.message}</p>}
          {search.data?.length === 0 && <p className="quiet-copy">AniList found no matching manga.</p>}
          {search.data && search.data.length > 0 && (
            <ul className="series-match__results">
              {search.data.map((match) => (
                <li key={match.anilist_id}>
                  <button
                    type="button"
                    aria-label={`Apply ${match.title} to ${suggestion.ready_count} candidates`}
                    disabled={apply.isPending}
                    onClick={() => applyMatch(match)}
                  >
                    {match.cover_url ? <img src={match.cover_url} alt="" /> : <span />}
                    <strong>{match.title}</strong>
                    <small>{[match.author, match.year].filter(Boolean).join(" · ")}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {apply.error && <p className="form-error">{apply.error.message}</p>}
        </div>
      )}
    </li>
  );
}
