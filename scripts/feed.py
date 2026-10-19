"""Deterministic, source-attributed feed builder. No model API or credentials."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "GeekNewsFeed/1.0 (+https://github.com/glebone/geek_news)"
MAX_BYTES = 2_000_000
CATEGORIES = {"linux", "macos", "ios", "android", "hardware", "programming",
              "functional_programming", "retro_computing", "retro_gaming", "ai",
              "agent_tools", "open_source"}
TYPES = {"news", "release", "technical_article", "research", "rumor", "project"}
STOP = {"the", "and", "for", "with", "from", "this", "that", "into", "your", "about", "their", "have", "will"}


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


class ArticleLead(HTMLParser):
    def __init__(self):
        super().__init__()
        self.descriptions = []
        self.paragraphs = []
        self.article_depth = 0
        self.in_paragraph = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("name", "").lower() == "description" or attrs.get("property", "").lower() == "og:description"):
            self.descriptions.append(attrs.get("content", ""))
        if tag == "article":
            self.article_depth += 1
        if tag == "p" and self.article_depth:
            self.in_paragraph = True
            self.chunks = []

    def handle_endtag(self, tag):
        if tag == "p" and self.in_paragraph:
            self.paragraphs.append(" ".join(self.chunks))
            self.in_paragraph = False
        if tag == "article" and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data):
        if self.in_paragraph:
            self.chunks.append(data)


def clean(value):
    parser = Text()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()


def fetch(url, *, max_bytes=MAX_BYTES):
    if urlparse(url).scheme != "https":
        raise ValueError("non-HTTPS URL")
    last = None
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html, application/json;q=0.8"})
            with urlopen(request, timeout=12) as response:
                final = response.url
                if urlparse(final).scheme != "https":
                    raise ValueError("redirected away from HTTPS")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("response too large")
                return final, data, response.headers.get_content_type()
        except (OSError, TimeoutError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last


def child_text(node, names):
    for child in node:
        if child.tag.split("}")[-1] in names and child.text:
            return child.text.strip()
    return ""


def parse_date(value):
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat(timespec="seconds")


def parse_source(source, now):
    _, data, _ = fetch(source["url"])
    root = ET.fromstring(data)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    result = []
    for item in items[:15]:
        title = clean(child_text(item, {"title"}))
        link = child_text(item, {"link"})
        if not link:
            for link_node in item:
                if link_node.tag.split("}")[-1] == "link" and link_node.attrib.get("rel", "alternate") == "alternate":
                    link = link_node.attrib.get("href", "")
                    break
        published = parse_date(child_text(item, {"pubDate", "published", "updated", "date"}))
        excerpt = clean(child_text(item, {"description", "summary", "content", "encoded"}))
        if not title or not link or not published:
            continue
        when = dt.datetime.fromisoformat(published)
        if when > now + dt.timedelta(hours=1) or when < now - dt.timedelta(hours=72):
            continue
        result.append({"title": title, "url": link, "excerpt": excerpt, "published_at": published, "source_config": source})
    return result


def normalized_url(url):
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("invalid HTTPS URL")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunparse(("https", parts.hostname.lower(), parts.path.rstrip("/") or "/", "", urlencode(query), ""))


def words(text):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+-]*", text) if len(w) > 3 and w.lower() not in STOP]


def summarize(excerpt, page):
    # Deliberately extractive: no LLM is used and no claims are added.
    lead = ArticleLead()
    lead.feed(page)
    for candidate in [excerpt, *lead.descriptions, *lead.paragraphs]:
        tokens = clean(candidate).split()
        if 25 <= len(tokens) <= 55:
            return " ".join(tokens)
        if len(tokens) > 55:
            return " ".join(tokens[:55]).rstrip(" ,;:") + "."
    raise ValueError("no 25-word source-authored lead")


def inspect_candidate(candidate):
    url, data, content_type = fetch(candidate["url"])
    url = normalized_url(url)
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise ValueError("destination is not an article page")
    page = data.decode("utf-8", errors="replace")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    page_title = clean(title_match.group(1)) if title_match else ""
    title_words = set(words(candidate["title"]))
    if not title_words or len(title_words & set(words(page_title))) < max(1, len(title_words) // 3):
        raise ValueError("article title not confirmed at destination")
    if re.search(r"\b(404|not found|page unavailable)\b", page_title, re.I):
        raise ValueError("soft 404")
    excerpt_words = set(words(candidate["excerpt"]))
    page_words = set(words(clean(page)))
    if excerpt_words and len(excerpt_words & page_words) < min(2, len(excerpt_words)):
        raise ValueError("feed excerpt not confirmed at destination")
    source = candidate["source_config"]
    summary = summarize(candidate["excerpt"], page)
    article = {"title": candidate["title"], "summary": summary, "url": url,
               "source": source["name"], "published_at": candidate["published_at"],
               "category": source["category"], "tags": source["tags"],
               "article_type": source["article_type"]}
    return article


def validate(edition, *, allow_seed=False):
    if set(edition) != {"schema_version", "date", "generated_at", "count", "articles"} or edition["schema_version"] != 1:
        raise ValueError("invalid top-level contract")
    dt.date.fromisoformat(edition["date"])
    generated = dt.datetime.fromisoformat(edition["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo != dt.timezone.utc:
        raise ValueError("generated_at must be UTC")
    articles = edition["articles"]
    if edition["count"] != len(articles) or (not allow_seed and not 10 <= len(articles) <= 20):
        raise ValueError("invalid article count")
    urls, events, domains, categories = set(), set(), {}, {}
    for article in articles:
        if set(article) != {"title", "summary", "url", "source", "published_at", "category", "tags", "article_type"}:
            raise ValueError("invalid article fields")
        url = normalized_url(article["url"])
        event = " ".join(sorted(set(words(article["title"]))))
        if url in urls or event in events:
            raise ValueError("duplicate article")
        urls.add(url)
        events.add(event)
        domain = urlparse(url).hostname
        domains[domain] = domains.get(domain, 0) + 1
        categories[article["category"]] = categories.get(article["category"], 0) + 1
        if domains[domain] > 3 or categories[article["category"]] > 4:
            raise ValueError("source or category cap exceeded")
        if article["category"] not in CATEGORIES or article["article_type"] not in TYPES:
            raise ValueError("invalid category or type")
        tags = article["tags"]
        if not isinstance(tags, list) or not 2 <= len(tags) <= 5 or len(set(tags)) != len(tags) or any(tag != tag.lower() for tag in tags):
            raise ValueError("invalid tags")
        if not 25 <= len(article["summary"].split()) <= 55:
            raise ValueError("invalid summary length")
        if article["article_type"] == "rumor" and not re.search(r"\b(rumou?r|report|unconfirmed)\b", article["title"] + " " + article["summary"], re.I):
            raise ValueError("unqualified rumor")
        if article["published_at"] is not None and dt.datetime.fromisoformat(article["published_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("publication timestamp lacks offset")
    return True


def recent_urls(now):
    seen = set()
    for offset in range(1, 8):
        date = (now.astimezone(ZoneInfo("Europe/Kyiv")).date() - dt.timedelta(days=offset)).isoformat()
        path = ROOT / "news" / "archive" / f"{date}.json"
        if path.exists():
            seen.update(normalized_url(article["url"]) for article in json.loads(path.read_text())["articles"])
    return seen


def build(now=None, *, sources=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    sources = sources or json.loads((ROOT / "config" / "sources.json").read_text())
    candidates, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(parse_source, source, now): source["name"] for source in sources}
        for future in concurrent.futures.as_completed(futures):
            try:
                candidates.extend(future.result())
            except Exception as exc:
                errors.append(f"{futures[future]}: {type(exc).__name__}: {exc}")
    candidates.sort(key=lambda item: item["published_at"], reverse=True)
    selected, rejected, selected_sources = [], {}, {}
    seen = recent_urls(now)
    event_keys, domain_counts, category_counts = set(), {}, {}
    for candidate in candidates[:100]:
        if len(selected) >= 20:
            break
        try:
            article = inspect_candidate(candidate)
            url = article["url"]
            event = " ".join(sorted(set(words(article["title"]))))
            domain = urlparse(url).hostname
            category = article["category"]
            if url in seen or event in event_keys:
                raise ValueError("duplicate or recent archive")
            if domain_counts.get(domain, 0) >= 3 or category_counts.get(category, 0) >= 4:
                raise ValueError("source or category cap")
            selected.append(article)
            selected_sources[article["source"]] = selected_sources.get(article["source"], 0) + 1
            seen.add(url)
            event_keys.add(event)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
        except Exception as exc:
            key = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            rejected[key] = rejected.get(key, 0) + 1
    date = now.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
    edition = {"schema_version": 1, "date": date, "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
               "count": len(selected), "articles": selected}
    status = {"route": "github-actions-deterministic", "date": date, "candidates": len(candidates),
              "selected": len(selected), "selected_sources": selected_sources,
              "rejected": rejected, "source_errors": errors}
    (ROOT / ".feed-run-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(status, ensure_ascii=False), file=sys.stderr)
    validate(edition)
    return edition


def publish(edition):
    data = (json.dumps(edition, ensure_ascii=False, indent=2) + "\n").encode()
    if len(data) > 200_000:
        raise ValueError("edition too large")
    latest = ROOT / "news" / "latest.json"
    archive = ROOT / "news" / "archive" / f"{edition['date']}.json"
    if latest.exists():
        previous = json.loads(latest.read_text())
        if previous["date"] == edition["date"] and previous["articles"] == edition["articles"]:
            print("unchanged edition; no files replaced")
            return
    latest.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Both paths are replaced only after the complete edition passes validation.
    for path in (latest, archive):
        temp = path.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(path)
    print(f"sha256={hashlib.sha256(data).hexdigest()} count={edition['count']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "validate"])
    parser.add_argument("path", nargs="?")
    args = parser.parse_args()
    if args.command == "build":
        edition = build()
        if edition["count"] < 15:
            print(f"degraded edition: {edition['count']} items", file=sys.stderr)
        publish(edition)
    else:
        path = Path(args.path or ROOT / "news" / "latest.json")
        edition = json.loads(path.read_text())
        validate(edition, allow_seed=edition["count"] == 0)
        print(f"valid count={edition['count']}")


if __name__ == "__main__":
    main()
