"""Turn fetched HTML into token-efficient output for agents."""

import re
from urllib.parse import urljoin, urldefrag

import trafilatura
from bs4 import BeautifulSoup

NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")
# Page chrome dropped by the listing-page fallback, which keeps everything else.
CHROME_TAGS = ("nav", "footer", "aside", "form", "dialog", "button")
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "td", "th", "figcaption", "dt", "dd")
# Article extraction keeps little of homepages and listing pages; below this share of
# the page's visible text, the structure-preserving fallback is used instead.
MIN_EXTRACTED_SHARE = 0.2


def select_fragment(html: str, css_selector: str) -> str | None:
    """Return the outer HTML of all elements matching css_selector, or None if none match."""
    soup = BeautifulSoup(html, "lxml")
    nodes = soup.select(css_selector)
    if not nodes:
        return None
    return "<html><body>" + "".join(str(node) for node in nodes) + "</body></html>"


def _plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    return str(soup.body or soup)


def _inside_link(child, node) -> bool:
    for parent in child.parents:
        if parent is node:
            return False
        if parent.name == "a":
            return True
    return False


def _link(text: str, href: str, base_url: str) -> str:
    href = href.strip()
    if not text or not href or href.startswith(("javascript:", "#")):
        return text
    return f"[{text}]({urljoin(base_url, href)})"


def _inline(node, base_url: str, include_links: bool) -> str:
    if not include_links:
        return " ".join(node.get_text(" ").split())
    wrapper = node.find_parent("a", href=True)
    if wrapper is not None:  # e.g. <a href><h3>Headline</h3></a> on news and shop listings
        return _link(" ".join(node.get_text(" ").split()), wrapper["href"], base_url)
    parts = []
    for child in node.descendants:
        if isinstance(child, str) and not _inside_link(child, node):
            parts.append(child)
        elif getattr(child, "name", None) == "a":
            parts.append(f" {_link(' '.join(child.get_text(' ').split()), child.get('href', ''), base_url)} ")
    return " ".join("".join(parts).split())


def _listing_markdown(html: str, base_url: str, include_links: bool) -> str:
    """Convert every content block (headings, paragraphs, list items, cells) to Markdown,
    for homepages and listing pages where article extraction drops most of the content."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS + CHROME_TAGS):
        tag.decompose()
    for tag in soup.select('[role="navigation"], [aria-hidden="true"], [hidden]'):
        tag.decompose()
    lines: list[str] = []
    seen: set[str] = set()
    for node in (soup.body or soup).find_all(BLOCK_TAGS):
        if node.find(BLOCK_TAGS):
            continue  # its inner blocks are emitted on their own
        text = _inline(node, base_url, include_links)
        if not text or text in seen:
            continue
        seen.add(text)
        if node.name[0] == "h" and node.name[1:].isdigit():
            lines.append(f"\n{'#' * int(node.name[1])} {text}\n")
        elif node.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(f"\n{text}\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _links(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href, _ = urldefrag(urljoin(base_url, anchor["href"].strip()))
        if href.startswith(("http://", "https://")) and href not in seen:
            seen[href] = " ".join(anchor.get_text(" ").split())[:120]
    return "\n".join(f"- [{text or href}]({href})" for href, text in seen.items())


def convert(html: str, url: str, output_format: str, include_links: bool, whole_fragment: bool = False) -> str:
    """Convert HTML to markdown, text, cleaned html, or a link list.

    whole_fragment skips main-content detection, which would otherwise discard parts of
    a fragment the caller chose explicitly with a CSS selector.
    """
    if output_format == "html":
        return _clean_html(html)
    if output_format == "links":
        return _links(html, url)

    trafilatura_format = "markdown" if output_format == "markdown" else "txt"
    extracted = None
    if not whole_fragment:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format=trafilatura_format,
            include_links=include_links and output_format == "markdown",
            include_tables=True,
            include_images=False,
            favor_recall=True,
        )
    if not extracted or (not whole_fragment and len(extracted) < 5000):
        # Fragments, and pages where article extraction kept little (homepages, listings),
        # get a conversion that keeps every content block instead.
        if output_format == "markdown":
            fallback = _listing_markdown(html, url, include_links)
        else:
            fallback = _plain_text(html)
        if not extracted or len(extracted) < MIN_EXTRACTED_SHARE * len(fallback):
            extracted = fallback
    return extracted.strip()


def metadata(html: str, url: str) -> dict:
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is None:
        return {}
    fields = {
        "title": meta.title,
        "description": meta.description,
        "author": meta.author,
        "sitename": meta.sitename,
        "date": meta.date,
        "language": getattr(meta, "language", None),
    }
    return {key: value for key, value in fields.items() if value}


def paginate(content: str, offset: int, max_chars: int) -> tuple[str, int | None]:
    """Return a window of content and the offset of the next window, ending at a paragraph break if possible."""
    window = content[offset: offset + max_chars]
    end = offset + len(window)
    if end < len(content):
        cut = window.rfind("\n\n")
        if cut > max_chars // 2:
            window = window[:cut]
            end = offset + cut
        return window, end
    return window, None
