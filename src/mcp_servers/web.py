"""Web research MCP tools for Sidera.

Provides tools that allow agents to fetch and analyze web content.

Tools:
    1. fetch_web_page - Fetch a URL and extract text content
    2. web_search     - Search the web for information

Usage:
    # Auto-registered via @tool decorator on import
    import src.mcp_servers.web  # noqa: F401
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agent.tool_registry import tool
from src.mcp_servers.helpers import error_response, text_response

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: Fetch a web page
# ---------------------------------------------------------------------------

FETCH_WEB_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "The URL to fetch. Must be a fully-formed URL (e.g. 'https://www.example.com')."
            ),
        },
        "extract_mode": {
            "type": "string",
            "description": (
                "What to extract: 'text' for readable text content (default), "
                "'links' for all links on the page, 'all' for both."
            ),
            "enum": ["text", "links", "all"],
        },
    },
    "required": ["url"],
}


@tool(
    name="fetch_web_page",
    description=(
        "Fetch a web page and extract its text content. Use this to research "
        "websites, analyze business pages, read documentation, or gather "
        "competitive intelligence. Returns the page's readable text content "
        "stripped of HTML. Useful for analyzing business websites, reading "
        "about services and offerings, and understanding positioning."
    ),
    input_schema=FETCH_WEB_PAGE_SCHEMA,
)
async def fetch_web_page(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a web page and return its text content."""
    url = args.get("url", "").strip()
    extract_mode = args.get("extract_mode", "text")

    if not url:
        return error_response("URL is required.")

    # Ensure URL has a scheme
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    logger.info("tool.fetch_web_page", url=url, mode=extract_mode)

    try:
        from html.parser import HTMLParser

        import httpx

        # Simple HTML-to-text parser
        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts: list[str] = []
                self.links: list[dict[str, str]] = []
                self._skip = False
                self._skip_tags = frozenset({"script", "style", "noscript", "head", "svg"})
                self._current_href: str = ""

            def handle_starttag(self, tag, attrs):
                if tag in self._skip_tags:
                    self._skip = True
                attrs_dict = dict(attrs)
                if tag == "a" and "href" in attrs_dict:
                    self._current_href = attrs_dict["href"]
                # Add line breaks for block elements
                if tag in (
                    "p",
                    "div",
                    "br",
                    "h1",
                    "h2",
                    "h3",
                    "h4",
                    "h5",
                    "h6",
                    "li",
                    "tr",
                    "section",
                    "article",
                    "header",
                    "footer",
                ):
                    self.text_parts.append("\n")

            def handle_endtag(self, tag):
                if tag in self._skip_tags:
                    self._skip = False
                if tag == "a" and self._current_href:
                    self._current_href = ""

            def handle_data(self, data):
                if not self._skip:
                    text = data.strip()
                    if text:
                        self.text_parts.append(text)
                        if self._current_href:
                            self.links.append({"text": text, "url": self._current_href})

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": ("Mozilla/5.0 (compatible; SideraBot/1.0; +https://sidera.ai)"),
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return text_response(
                f"URL returned non-HTML content type: {content_type}. "
                f"Status: {response.status_code}"
            )

        html = response.text

        # Parse HTML
        parser = _TextExtractor()
        parser.feed(html)

        # Clean up text — collapse whitespace
        raw_text = " ".join(parser.text_parts)
        import re

        text = re.sub(r"\n{3,}", "\n\n", raw_text)
        text = re.sub(r" {2,}", " ", text)
        text = text.strip()

        # Truncate if very long
        max_chars = 15000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[... content truncated at 15,000 chars]"

        # Build response based on mode
        parts = [f"URL: {url}\nStatus: {response.status_code}\n"]

        if extract_mode in ("text", "all"):
            parts.append(f"--- Page Content ---\n{text}")

        if extract_mode in ("links", "all"):
            if parser.links:
                link_lines = [f"  - {lnk['text']}: {lnk['url']}" for lnk in parser.links[:50]]
                parts.append(f"\n--- Links ({len(parser.links)} found) ---\n")
                parts.append("\n".join(link_lines))
                if len(parser.links) > 50:
                    parts.append(f"\n  ... and {len(parser.links) - 50} more")

        return text_response("\n".join(parts))

    except httpx.HTTPStatusError as exc:
        return error_response(f"HTTP error {exc.response.status_code} fetching {url}: {exc}")
    except httpx.TimeoutException:
        return error_response(f"Timeout fetching {url} (30s limit)")
    except Exception as exc:
        logger.error("tool.fetch_web_page.error", url=url, error=str(exc))
        return error_response(f"Failed to fetch {url}: {exc}")


# ---------------------------------------------------------------------------
# Tool 2: Web search
# ---------------------------------------------------------------------------

WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query to run.",
        },
        "num_results": {
            "type": "integer",
            "description": "Number of results to return (default 5, max 10).",
        },
    },
    "required": ["query"],
}


@tool(
    name="web_search",
    description=(
        "Search the web for information. Returns titles, URLs, and snippets "
        "from search results. Use this to research competitors, find industry "
        "data, discover market trends, or gather intelligence. Use fetch_web_page "
        "to read the full content of any result."
    ),
    input_schema=WEB_SEARCH_SCHEMA,
)
async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Search the web and return results."""
    query = args.get("query", "").strip()
    num_results = min(args.get("num_results", 5), 10)

    if not query:
        return error_response("Search query is required.")

    logger.info("tool.web_search", query=query, num_results=num_results)

    try:
        import httpx

        # Use DuckDuckGo HTML search (no API key required)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            response.raise_for_status()

        html = response.text

        # Parse DuckDuckGo results
        import re

        results = []
        # Match result blocks
        result_pattern = re.compile(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
            r'.*?class="result__snippet"[^>]*>(.*?)</(?:td|div)',
            re.DOTALL,
        )

        for match in result_pattern.finditer(html):
            raw_url = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()

            # DuckDuckGo wraps URLs in a redirect — extract actual URL
            if "uddg=" in raw_url:
                from urllib.parse import parse_qs, unquote, urlparse

                parsed = urlparse(raw_url)
                qs = parse_qs(parsed.query)
                actual_url = unquote(qs.get("uddg", [raw_url])[0])
            else:
                actual_url = raw_url

            if title:
                results.append({"title": title, "url": actual_url, "snippet": snippet})

            if len(results) >= num_results:
                break

        if not results:
            return text_response(f"No results found for: {query}\nTry a different search query.")

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
            lines.append("")

        lines.append("Use fetch_web_page to read the full content of any result.")
        return text_response("\n".join(lines))

    except Exception as exc:
        logger.error("tool.web_search.error", query=query, error=str(exc))
        return error_response(f"Search failed: {exc}")
