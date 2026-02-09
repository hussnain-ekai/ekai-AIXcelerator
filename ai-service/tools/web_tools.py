"""LangChain tool for fetching public documentation.

Fetches documentation from whitelisted domains (e.g. docs.snowflake.com),
converts HTML to Markdown, splits into sections, and returns only the
sections most relevant to the agent's query.

Runs entirely inside the container — no third-party APIs, no API keys.
In SPCS, outbound access is gated by External Access Integrations
(consumer-approved domain whitelist).

Context bloat prevention:
    - Pages are split into sections by headings
    - Sections are scored against the query
    - Only top sections returned, capped at ~1500 tokens
    - Processed pages cached in memory (24h TTL)
"""

import logging
import re
import time
from typing import Any

import httpx
from langchain_core.tools import tool
from markdownify import markdownify as md

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain whitelist — only these domains can be fetched
# In SPCS, the External Access Integration enforces this at the network level.
# This code-level check is defense-in-depth for local dev.
# ---------------------------------------------------------------------------

_ALLOWED_DOMAINS: set[str] = {
    "docs.snowflake.com",
}

# ---------------------------------------------------------------------------
# In-memory page cache — avoids re-fetching the same page within TTL
# Key: URL, Value: (timestamp, list of sections)
# ---------------------------------------------------------------------------

_page_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL_SECONDS = 86400  # 24 hours

# ---------------------------------------------------------------------------
# Output limits
# ---------------------------------------------------------------------------

_MAX_OUTPUT_CHARS = 6000  # ~1500 tokens
_MAX_FETCH_BYTES = 5_000_000  # 5 MB — reject absurdly large pages
_FETCH_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Stop words for keyword extraction
# ---------------------------------------------------------------------------

_STOP_WORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "and", "or", "not", "this", "that",
    "it", "as", "be", "has", "have", "had", "do", "does", "did", "will",
    "would", "can", "could", "should", "what", "how", "which", "when",
    "where", "who", "why", "about", "all", "each", "every", "any", "some",
    "no", "than", "too", "very", "just", "also", "more", "most", "other",
    "into", "over", "such", "only", "same", "so", "if", "but", "then",
    "up", "out", "its", "my", "your", "their", "our", "his", "her",
    "use", "used", "using", "see", "like", "new", "one", "two",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_domain_allowed(url: str) -> bool:
    """Check if the URL's domain is in the whitelist."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return hostname in _ALLOWED_DOMAINS
    except Exception:
        return False


def _extract_main_content(html: str) -> str:
    """Extract the main content area from an HTML page.

    Modern doc sites (Next.js, Docusaurus, Sphinx) wrap content in
    <main>, <article>, or role="main" elements. We extract that and
    discard navigation, sidebars, footers, and embedded JSON blobs
    like Next.js __NEXT_DATA__.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": "navigation"}):
        tag.decompose()
    for tag in soup.find_all("div", class_=re.compile(r"sidebar|nav|footer|header|menu", re.I)):
        tag.decompose()

    # Find main content: try <main>, <article>, role="main", or common doc classes
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"content|documentation|doc-body", re.I))
    )

    if main:
        return str(main)

    # Fallback: use the whole body
    body = soup.find("body")
    return str(body) if body else html


def _fetch_and_convert(url: str) -> str:
    """Fetch a URL and convert HTML to Markdown."""
    with httpx.Client(
        timeout=_FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "ekaiX-DocFetch/1.0"},
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()

        content_length = len(resp.content)
        if content_length > _MAX_FETCH_BYTES:
            raise ValueError(
                f"Page too large ({content_length:,} bytes). Max: {_MAX_FETCH_BYTES:,}."
            )

        content_type = resp.headers.get("content-type", "")

        # If already markdown, return as-is
        if "text/markdown" in content_type:
            return resp.text

        # Convert HTML to Markdown — extract main content first to remove noise
        if "text/html" in content_type or "<html" in resp.text[:500].lower():
            clean_html = _extract_main_content(resp.text)
            return md(clean_html, heading_style="ATX", strip=["script", "style"])

        # Plain text or other — return raw
        return resp.text


_MAX_SECTION_CHARS = 3000  # Sections larger than this get sub-split


def _is_real_heading(heading_text: str) -> bool:
    """Distinguish real markdown headings from YAML/code comments.

    Real headings typically contain anchor links [¶], start with a capital
    letter, or are multi-word titles. YAML comments like "# A comment about
    tables" inside code blocks are lowercase descriptive text.
    """
    if "¶" in heading_text:
        return True
    # Strip any link syntax
    clean = re.sub(r"\[.*?\]\(.*?\)", "", heading_text).strip()
    if not clean:
        return False
    # Real headings start with uppercase and are typically short titles
    if clean[0].isupper() and len(clean) < 200:
        return True
    return False


def _split_into_sections(markdown: str) -> list[dict[str, str]]:
    """Split markdown into sections by headings.

    Returns a list of dicts with 'heading' and 'content' keys.
    Filters out YAML/code comments that look like headings.
    Oversized sections are sub-split to prevent any single section
    from dominating keyword scoring.
    """
    raw_sections: list[dict[str, str]] = []
    current_heading = "Introduction"
    current_lines: list[str] = []

    for line in markdown.split("\n"):
        heading_match = re.match(r"^(#{1,4})\s+(.+)", line)
        if heading_match and _is_real_heading(heading_match.group(2)):
            content = "\n".join(current_lines).strip()
            if content:
                raw_sections.append({
                    "heading": current_heading,
                    "content": content,
                })
            raw_heading = heading_match.group(2).strip()
            # Clean anchor links: "Title[¶](#anchor ...)" → "Title"
            current_heading = re.sub(r"\[¶\].*$", "", raw_heading).strip()
            current_lines = []
        else:
            current_lines.append(line)

    content = "\n".join(current_lines).strip()
    if content:
        raw_sections.append({
            "heading": current_heading,
            "content": content,
        })

    # Sub-split oversized sections so they don't dominate scoring
    sections: list[dict[str, str]] = []
    for sec in raw_sections:
        if len(sec["content"]) <= _MAX_SECTION_CHARS:
            sections.append(sec)
        else:
            # Split on double-newlines (paragraph boundaries)
            paragraphs = re.split(r"\n\n+", sec["content"])
            chunk_lines: list[str] = []
            chunk_chars = 0
            part_num = 1
            for para in paragraphs:
                if chunk_chars + len(para) > _MAX_SECTION_CHARS and chunk_lines:
                    sections.append({
                        "heading": f"{sec['heading']} (part {part_num})",
                        "content": "\n\n".join(chunk_lines).strip(),
                    })
                    part_num += 1
                    chunk_lines = []
                    chunk_chars = 0
                chunk_lines.append(para)
                chunk_chars += len(para)
            if chunk_lines:
                sections.append({
                    "heading": f"{sec['heading']} (part {part_num})" if part_num > 1 else sec["heading"],
                    "content": "\n\n".join(chunk_lines).strip(),
                })

    return sections


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a query string."""
    words = re.findall(r"[a-z_]+", query.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def _score_section(section: dict[str, str], keywords: list[str]) -> float:
    """Score a section's relevance to the query keywords.

    Heading matches are weighted 3x. Content keyword frequency is counted
    but capped per keyword to avoid long sections dominating.
    """
    heading_lower = section["heading"].lower()
    content_lower = section["content"].lower()
    full_text = heading_lower + " " + content_lower

    score = 0.0
    for kw in keywords:
        # Heading match — high signal
        if kw in heading_lower:
            score += 3.0
        # Content frequency — capped at 5 per keyword
        count = full_text.count(kw)
        if count > 0:
            score += min(count, 5)

    # Slight penalty for very long sections (prefer concise)
    section_len = len(section["content"])
    if section_len > 3000:
        score *= 0.9

    return score


def _get_cached_sections(url: str) -> list[dict[str, str]] | None:
    """Return cached sections if they exist and haven't expired."""
    if url in _page_cache:
        timestamp, sections = _page_cache[url]
        if time.time() - timestamp < _CACHE_TTL_SECONDS:
            return sections
        del _page_cache[url]
    return None


def _cache_sections(url: str, sections: list[dict[str, str]]) -> None:
    """Store sections in the in-memory cache."""
    _page_cache[url] = (time.time(), sections)


def _select_relevant_sections(
    sections: list[dict[str, str]], query: str
) -> str:
    """Score sections against the query and return top ones within token limit."""
    keywords = _extract_keywords(query)

    if not keywords:
        # No meaningful keywords — return first section
        if sections:
            text = f"## {sections[0]['heading']}\n{sections[0]['content']}"
            return text[:_MAX_OUTPUT_CHARS]
        return "No content found."

    scored = [(s, _score_section(s, keywords)) for s in sections]
    scored.sort(key=lambda x: x[1], reverse=True)

    result_parts: list[str] = []
    total_chars = 0

    for section, score in scored:
        if score <= 0:
            continue
        section_text = f"## {section['heading']}\n{section['content']}"
        if total_chars + len(section_text) > _MAX_OUTPUT_CHARS:
            remaining = _MAX_OUTPUT_CHARS - total_chars
            if remaining > 200:
                result_parts.append(section_text[:remaining] + "...")
            break
        result_parts.append(section_text)
        total_chars += len(section_text)

    if not result_parts:
        return "No sections matched the query. Try rephrasing."

    return "\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool
def fetch_documentation(url: str, query: str) -> str:
    """Fetch public documentation and return sections relevant to your query.

    Use this to look up current Snowflake documentation before generating
    or validating semantic view YAML. The tool fetches the page, extracts
    relevant sections, and returns only what matches your query (~1500 tokens max).

    Results are cached for 24 hours — repeated calls to the same URL are instant.

    Args:
        url: Documentation URL to fetch (must be on docs.snowflake.com)
        query: What you need to know (e.g. "supported expressions in semantic view facts and metrics")

    Returns:
        Relevant documentation sections as plain text
    """
    # Domain check
    if not _is_domain_allowed(url):
        return f"Error: Domain not allowed. Only these domains are permitted: {', '.join(sorted(_ALLOWED_DOMAINS))}"

    # Check cache first
    sections = _get_cached_sections(url)
    if sections is not None:
        logger.info("fetch_documentation: cache hit for %s (%d sections)", url, len(sections))
    else:
        # Fetch and process
        try:
            logger.info("fetch_documentation: fetching %s", url)
            markdown = _fetch_and_convert(url)
            if not markdown or len(markdown.strip()) < 100:
                return "Error: Page returned empty or minimal content. It may require JavaScript rendering."
            sections = _split_into_sections(markdown)
            if not sections:
                return "Error: Could not split page into sections. Returning raw excerpt.\n\n" + markdown[:_MAX_OUTPUT_CHARS]
            _cache_sections(url, sections)
            logger.info("fetch_documentation: cached %d sections from %s", len(sections), url)
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} fetching {url}"
        except httpx.TimeoutException:
            return f"Error: Timeout fetching {url} (limit: {_FETCH_TIMEOUT_SECONDS}s)"
        except Exception as e:
            logger.warning("fetch_documentation error for %s: %s", url, e)
            return f"Error fetching documentation: {e}"

    # Score and select relevant sections
    return _select_relevant_sections(sections, query)
