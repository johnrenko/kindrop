"""Manga metadata lookup against the public AniList GraphQL API."""

from dataclasses import dataclass
from typing import Any

import httpx

ANILIST_ENDPOINT = "https://graphql.anilist.co"

_SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 8) {
    media(search: $search, type: MANGA, sort: SEARCH_MATCH) {
      id
      format
      startDate { year }
      title { romaji english native }
      coverImage { extraLarge large }
      staff(perPage: 6, sort: RELEVANCE) {
        edges { role node { name { full } } }
      }
    }
  }
}
"""


class AniListError(RuntimeError):
    pass


@dataclass(frozen=True)
class MangaMatch:
    anilist_id: int
    title: str
    native_title: str | None
    author: str | None
    cover_url: str | None
    format: str | None
    year: int | None


def _author_from_staff(staff: dict[str, Any] | None) -> str | None:
    for edge in (staff or {}).get("edges") or []:
        role = (edge.get("role") or "").lower()
        name = ((edge.get("node") or {}).get("name") or {}).get("full")
        if name and "story" in role:
            return name
    for edge in (staff or {}).get("edges") or []:
        name = ((edge.get("node") or {}).get("name") or {}).get("full")
        if name:
            return name
    return None


def parse_search_response(payload: dict[str, Any]) -> list[MangaMatch]:
    media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
    matches = []
    for item in media:
        titles = item.get("title") or {}
        title = titles.get("english") or titles.get("romaji")
        if not title:
            continue
        cover = item.get("coverImage") or {}
        matches.append(
            MangaMatch(
                anilist_id=item["id"],
                title=title,
                native_title=titles.get("native"),
                author=_author_from_staff(item.get("staff")),
                cover_url=cover.get("extraLarge") or cover.get("large"),
                format=item.get("format"),
                year=(item.get("startDate") or {}).get("year"),
            )
        )
    return matches


def search_manga(query: str) -> list[MangaMatch]:
    try:
        response = httpx.post(
            ANILIST_ENDPOINT,
            json={"query": _SEARCH_QUERY, "variables": {"search": query}},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise AniListError("AniList could not be reached") from error
    try:
        return parse_search_response(response.json())
    except (ValueError, KeyError, TypeError) as error:
        raise AniListError("AniList returned an unexpected response") from error


def download_cover(url: str) -> bytes:
    try:
        response = httpx.get(url, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise AniListError("The cover image could not be downloaded") from error
    if len(response.content) > 10 * 1024 * 1024:
        raise AniListError("The cover image is unexpectedly large")
    return response.content
