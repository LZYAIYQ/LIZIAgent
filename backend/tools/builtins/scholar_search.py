"""scholar_search: search Google Scholar for academic papers.

Provider chain
--------------

1. **SerpAPI** (primary) — ``GET https://serpapi.com/search?engine=google_scholar``.
   Official API, handles anti-bot internally, returns structured JSON.
   Requires ``SERPAPI_API_KEY``. Free tier: 100 searches/month.

2. **scholarly + proxy** (fallback) — when SerpAPI key is missing or the
   request fails, falls back to the ``scholarly`` library with optional
   proxy via ``LZAGENT_SCHOLAR_PROXY``.

Proxy format (for scholarly fallback)
--------------------------------------

    http://127.0.0.1:7890
    socks5://127.0.0.1:1080
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx
from loguru import logger

from ..base import Tool, ToolPermission, ToolResult

SERPAPI_ENDPOINT = "https://serpapi.com/search"
DEFAULT_RESULTS = 5
MAX_RESULTS_CAP = 10
DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Provider 1: SerpAPI
# ---------------------------------------------------------------------------

async def _try_serpapi(
    client: httpx.AsyncClient,
    query: str,
    top_k: int,
    api_key: str,
) -> list[dict[str, str]]:
    """Search Google Scholar via SerpAPI."""
    params = {
        "engine": "google_scholar",
        "q": query,
        "api_key": api_key,
        "num": top_k,
        "hl": "zh-CN",
    }
    resp = await client.get(SERPAPI_ENDPOINT, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"SerpAPI returned HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    organic = data.get("organic_results", [])
    if not organic:
        return []

    results: list[dict[str, str]] = []
    for item in organic[:top_k]:
        title = str(item.get("title", "")).strip()
        if not title:
            continue

        publication_info = item.get("publication_info", {})
        authors_list = publication_info.get("authors", [])
        if isinstance(authors_list, list) and authors_list:
            authors_str = ", ".join(
                str(a.get("name", "")) for a in authors_list[:3]
            )
            if len(authors_list) > 3:
                authors_str += " et al."
        else:
            summary = str(publication_info.get("summary", ""))
            authors_str = summary.split(" - ")[0].strip() if " - " in summary else ""

        year = ""
        summary_text = str(publication_info.get("summary", ""))
        if summary_text:
            import re
            year_match = re.search(r"\b(19|20)\d{2}\b", summary_text)
            if year_match:
                year = year_match.group()

        snippet = str(item.get("snippet", "")).strip()
        inline_links = item.get("inline_links", {})
        resources = inline_links.get("resources", [])
        url = ""
        if resources:
            url = str(resources[0].get("link", "")).strip()
        if not url:
            link = item.get("link", "")
            if link:
                url = str(link).strip()

        cited_by = ""
        inline = item.get("inline_links", {})
        cited_info = inline.get("cited_by", {})
        if isinstance(cited_info, dict):
            cited_by = str(cited_info.get("total", ""))

        results.append({
            "title": title,
            "authors": authors_str,
            "year": year,
            "abstract": snippet,
            "citations": cited_by or "0",
            "url": url,
        })

    return results


# ---------------------------------------------------------------------------
# Provider 2: scholarly (fallback)
# ---------------------------------------------------------------------------

def _configure_scholarly_proxy() -> None:
    proxy = (
        os.environ.get("LZAGENT_SCHOLAR_PROXY")
        or os.environ.get("SCHOLAR_PROXY")
        or ""
    ).strip()
    if proxy:
        try:
            from scholarly import use_proxy
            use_proxy(http=proxy, https=proxy)
            logger.info("[scholar_search] scholarly proxy configured: {}", proxy)
        except Exception as exc:
            logger.warning("[scholar_search] failed to configure scholarly proxy: {}", exc)


def _search_scholarly(query: str, top_k: int) -> list[dict[str, str]]:
    from scholarly import scholarly

    results: list[dict[str, str]] = []
    search_query = scholarly.search_pubs(query)

    for _ in range(top_k):
        try:
            pub = next(search_query)
        except StopIteration:
            break
        except Exception as exc:
            logger.warning("[scholar_search] scholarly iteration error: {}", exc)
            break

        bib = pub.get("bib", {})
        title = str(bib.get("title", "")).strip()
        if not title:
            continue

        authors = bib.get("author", [])
        if isinstance(authors, list):
            authors_str = ", ".join(str(a) for a in authors[:3])
            if len(authors) > 3:
                authors_str += " et al."
        else:
            authors_str = str(authors)

        year = str(bib.get("pub_year", "")).strip()
        abstract = str(bib.get("abstract", "")).strip()
        num_citations = pub.get("num_citations", 0)
        url = str(pub.get("pub_url") or pub.get("eprint_url") or "").strip()

        results.append({
            "title": title,
            "authors": authors_str,
            "year": year,
            "abstract": abstract,
            "citations": str(num_citations) if num_citations else "0",
            "url": url,
        })

    return results


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ScholarSearchTool(Tool):
    name = "scholar_search"
    description = (
        "Search Google Scholar for academic papers. Returns title, authors,"
        " year, citation count, abstract snippet, and URL. Use this when the"
        " user asks to find papers, literature, surveys, or academic references."
        " Supports SerpAPI (set SERPAPI_API_KEY) or scholarly library with"
        " proxy (set LZAGENT_SCHOLAR_PROXY)."
    )
    permission = ToolPermission.SAFE
    is_read_only = True
    is_concurrency_safe = True
    is_destructive = False
    max_result_chars = 6_000
    search_hint = "scholar academic paper research literature google scholar 论文 学术 搜索"
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query (e.g. 'transformer attention mechanism',"
                    " 'RAG evaluation survey', 'deepfake detection')."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": (
                    f"Number of results to return (1-{MAX_RESULTS_CAP},"
                    f" default {DEFAULT_RESULTS})."
                ),
                "minimum": 1,
                "maximum": MAX_RESULTS_CAP,
            },
        },
        "required": ["query"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, content="", error="query is required")

        try:
            top_k = int(arguments.get("top_k") or DEFAULT_RESULTS)
        except (TypeError, ValueError):
            top_k = DEFAULT_RESULTS
        top_k = max(1, min(top_k, MAX_RESULTS_CAP))

        serpapi_key = (
            os.environ.get("SERPAPI_API_KEY")
            or os.environ.get("SERPAPI_KEY")
            or ""
        ).strip()

        errors: list[str] = []

        # --- Provider 1: SerpAPI ---
        if serpapi_key:
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    results = await _try_serpapi(client, query, top_k, serpapi_key)
                if results:
                    logger.info(
                        "[scholar_search] serpapi returned {} results", len(results)
                    )
                    return _render_results(query, results, provider="SerpAPI")
                errors.append("serpapi: empty results")
            except Exception as exc:
                errors.append(f"serpapi: {type(exc).__name__}: {exc}")
                logger.warning("[scholar_search] serpapi failed: {}", exc)
        else:
            errors.append("serpapi: disabled (no SERPAPI_API_KEY)")

        # --- Provider 2: scholarly fallback ---
        try:
            _configure_scholarly_proxy()
            results = await asyncio.to_thread(_search_scholarly, query, top_k)
            if results:
                logger.info(
                    "[scholar_search] scholarly returned {} results", len(results)
                )
                return _render_results(query, results, provider="scholarly")
            errors.append("scholarly: empty results")
        except ImportError:
            errors.append("scholarly: not installed (pip install scholarly)")
        except Exception as exc:
            errors.append(f"scholarly: {type(exc).__name__}: {exc}")
            logger.warning("[scholar_search] scholarly failed: {}", exc)

        # --- All failed ---
        error_detail = "; ".join(errors)
        hint = ""
        if "no SERPAPI_API_KEY" in error_detail:
            hint = (
                " For best results, get a free API key at https://serpapi.com"
                " (100 searches/month free) and set SERPAPI_API_KEY in .env."
            )
        return ToolResult(
            ok=False,
            content="",
            error=f"All Google Scholar providers failed ({error_detail}).{hint}",
        )


def _render_results(
    query: str,
    results: list[dict[str, str]],
    *,
    provider: str = "Google Scholar",
) -> ToolResult:
    lines = [
        f"Google Scholar results for: {query}",
        f"(via {provider}, found {len(results)} papers)",
        "",
    ]
    for idx, item in enumerate(results, 1):
        lines.append(f"{idx}. {item['title']}")
        if item.get("authors"):
            lines.append(f"   Authors: {item['authors']}")
        meta_parts = []
        if item.get("year"):
            meta_parts.append(f"Year: {item['year']}")
        if item.get("citations") and item["citations"] != "0":
            meta_parts.append(f"Citations: {item['citations']}")
        if meta_parts:
            lines.append(f"   {' | '.join(meta_parts)}")
        if item.get("abstract"):
            abstract = item["abstract"]
            if len(abstract) > 200:
                abstract = abstract[:200] + "..."
            lines.append(f"   {abstract}")
        if item.get("url"):
            lines.append(f"   URL: {item['url']}")
        lines.append("")

    return ToolResult(ok=True, content="\n".join(lines).strip())
