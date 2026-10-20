"""Deterministic, source-attributed feed builder. No model API or credentials."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
from html.parser import HTMLParser
from itertools import zip_longest
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
CATEGORIES_V1 = {"linux", "macos", "ios", "android", "hardware", "programming",
                 "functional_programming", "retro_computing", "retro_gaming", "ai",
                 "agent_tools", "open_source"}
CATEGORIES = CATEGORIES_V1 | {"security", "privacy", "web_platform", "cloud"}
EXCLUDED_CATEGORIES = {"security", "privacy"}
TYPES = {"news", "release", "technical_article", "research", "rumor", "project"}
STOP = {"the", "and", "for", "with", "from", "this", "that", "into", "your", "about", "their", "have", "will"}
MIN_ARTICLES = 10
TARGET_ARTICLES = 20
MAX_ARTICLES = 25
MAX_PER_DOMAIN = 3
MAX_PER_CATEGORY = 4
SELECTION_CATEGORY_LIMITS = {
    "linux": 6, "open_source": 6, "programming": 5,
    "functional_programming": 4, "ai": 4, "agent_tools": 5,
    "hardware": 1, "macos": 2, "ios": 2, "android": 2,
    "retro_gaming": 2,
}
PREFERRED_CATEGORIES = ("open_source", "linux", "programming", "functional_programming",
                        "ai", "agent_tools", "retro_computing", "retro_gaming")


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
        self.content_depth = 0
        self.content_sections = 0
        self.in_paragraph = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("name", "").lower() == "description" or attrs.get("property", "").lower() == "og:description"):
            self.descriptions.append(attrs.get("content", ""))
        if tag == "article":
            self.article_depth += 1
        if tag == "div":
            if self.content_depth:
                self.content_depth += 1
            elif any(name in attrs.get("class", "").split() for name in ("post-content", "entry-content", "article-content")):
                self.content_depth = 1
        if tag == "section":
            if self.content_sections:
                self.content_sections += 1
            elif "post__content" in attrs.get("class", "").split():
                self.content_sections = 1
        if tag == "p" and (self.article_depth or self.content_depth or self.content_sections):
            self.in_paragraph = True
            self.chunks = []

    def handle_endtag(self, tag):
        if tag == "p" and self.in_paragraph:
            self.paragraphs.append(" ".join(self.chunks))
            self.in_paragraph = False
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag == "div" and self.content_depth:
            self.content_depth -= 1
        if tag == "section" and self.content_sections:
            self.content_sections -= 1

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
        published = parse_date(child_text(item, {"pubDate", "published", "date"}) or child_text(item, {"updated"}))
        excerpt_html = child_text(item, {"description", "summary", "content", "encoded"})
        excerpt = clean(excerpt_html)
        if not title or not link or not published:
            continue
        keywords = source.get("title_keywords", [])
        if keywords and not any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", title, re.I) for word in keywords):
            continue
        if source.get("title_exclude_regex") and re.search(source["title_exclude_regex"], title, re.I):
            continue
        if re.search(r"\b(rumou?r|reportedly|leaked?|could|might|expected to)\b", title, re.I):
            continue
        if source["name"] in {"9to5Mac", "MacRumors", "9to5Google"} and re.search(r"\b(lawsuit|verdict|patent|court|deal|discount|sale|price|pre-order|delivery|walk-in|pickup|shipping)\b|\$\d", title, re.I):
            continue
        when = dt.datetime.fromisoformat(published)
        if when > now + dt.timedelta(hours=1) or when < now - dt.timedelta(hours=72):
            continue
        result.append({"title": title, "url": link, "excerpt": excerpt,
                       "excerpt_html": excerpt_html, "published_at": published,
                       "source_config": source})
    return result


def normalized_url(url):
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("invalid HTTPS URL")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunparse(("https", parts.hostname.lower(), parts.path.rstrip("/") or "/", "", urlencode(query), ""))


def words(text):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+.-]*|\d+(?:\.\d+)*", text)
            if (len(w) >= 3 or w[0].isdigit()) and w.lower() not in STOP]


def event_key(title):
    return frozenset(words(title))


def same_event(key, existing):
    for previous in existing:
        numbers = {word for word in key if word[0].isdigit()}
        previous_numbers = {word for word in previous if word[0].isdigit()}
        if numbers != previous_numbers:
            continue
        overlap = len(key & previous)
        if key == previous or (overlap >= 3 and
                               (overlap / len(key | previous) >= 0.67 or
                                overlap / min(len(key), len(previous)) >= 0.8)):
            return True
    return False


def summarize(excerpt, page, title=""):
    # Deliberately extractive: no LLM is used and no claims are added.
    lead = ArticleLead()
    lead.feed(page)
    candidates = []
    title_words = set(words(title))
    for candidate in [*lead.paragraphs, *lead.descriptions, excerpt]:
        candidate = clean(candidate)
        candidate = re.sub(r"https?://\S+", "", candidate).strip()
        if re.search(r"\b(affiliate|sponsored|eBay|Amazon|buy now|coupon|discount|subscribe|ready to try it|sign up|download the .+ app)\b", candidate, re.I):
            continue
        if re.search(r"\bthe post .+ appeared first on\b", candidate, re.I):
            candidate = re.split(r"\bthe post .+ appeared first on\b", candidate, flags=re.I)[0].strip()
        if candidate.endswith(("…", "...")):
            continue
        sentences = re.split(r"(?<=[.!?])\s+", candidate)
        summary = ""
        for sentence in sentences:
            next_summary = (summary + " " + sentence).strip()
            if len(next_summary.split()) > 55:
                break
            summary = next_summary
            if 25 <= len(summary.split()) <= 55 and re.search(r"[.!?][\"'”’)]*$", summary):
                overlap = len(title_words & set(words(summary)))
                if not title_words or overlap:
                    candidates.append((overlap, summary))
                break
    if not candidates:
        raise ValueError("no relevant 25-word source-authored lead")
    return max(candidates, key=lambda item: item[0])[1]


def summarize_release(excerpt_html):
    """Use a complete paragraph or bullet from source-authored release notes."""
    pieces = re.findall(r"<(?:p|li)\b[^>]*>(.*?)</(?:p|li)>", excerpt_html, re.I | re.S)
    for piece in pieces:
        sentence = clean(piece).strip()
        if re.search(r"\b(affiliate|sponsored|buy now|coupon|discount)\b", sentence, re.I):
            continue
        if 25 <= len(sentence.split()) <= 55:
            return sentence if sentence.endswith((".", "!", "?")) else sentence + "."
    raise ValueError("no complete 25-word release note")


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
    summary = (summarize_release(candidate.get("excerpt_html", "")) if source.get("github_release")
               else summarize(candidate["excerpt"], page, candidate["title"]))
    if source.get("github_release") and len(set(words(summary)) & page_words) < 5:
        raise ValueError("release note not confirmed at destination")
    if source["name"] in {"9to5Mac", "MacRumors", "9to5Google"} and re.search(r"\b(rumou?r(?:ed|s)?|reportedly|leaked?|could|might|likely|supposedly|expected to)\b", summary, re.I):
        raise ValueError("unconfirmed hardware rumor")
    category = source["category"]
    title = candidate["title"]
    if source["name"] in {"9to5Mac", "MacRumors"}:
        if re.search(r"\b(macOS|Mac OS)\b", title, re.I):
            category = "macos"
        elif re.search(r"\b(iOS|iPadOS)\b", title, re.I):
            category = "ios"
        else:
            category = "hardware"
    elif source["name"] == "Phoronix":
        if re.search(r"\b(Atari|Amiga|Commodore|m68k|vintage|retro)\b", title, re.I):
            category = "retro_computing"
        elif re.search(r"\b(Steam|Steam Deck|Proton|VKD3D|DXVK|Wine|Vulkan|XeSS)\b", title, re.I):
            category = "linux"
        elif re.search(r"\b(Intel|AMD|NVIDIA|Radeon|GeForce|GPU|CPU|XeSS)\b", title, re.I):
            category = "hardware"
    elif source["name"] in {"The Register", "OSNews", "Hackaday"}:
        if source["name"] == "Hackaday" and re.search(r"\b(Apple Intelligence|macOS|MacBook)\b", title, re.I):
            category = "macos"
        elif re.search(r"\b(Haskell|Lisp|Racket|OCaml|Elixir|Erlang|Clojure|F#)\b", title, re.I):
            category = "functional_programming"
        elif re.search(r"\b(Atari|Amiga|Commodore|vintage|retro|MS-DOS)\b", title, re.I):
            category = "retro_computing"
    elif source["name"] == "Simon Willison" and re.search(r"\b(GPT|Claude|Gemini|Llama|Qwen|Mistral|model)\b", title, re.I):
        category = "ai"
    article_type = "release" if source["name"] == "Simon Willison" and re.search(r"\b\d+\.\d+(?:\.\d+)?\b", candidate["title"]) else source["article_type"]
    tags = list(source["tags"])
    for pattern, tag in ((r"\bOpenBSD\b", "openbsd"), (r"\bNetBSD\b", "netbsd"),
                         (r"\bFreeBSD\b", "freebsd"), (r"\bHaiku\b", "haiku"),
                         (r"\bSteam\b", "steam"), (r"\bProton\b", "proton"),
                         (r"\bTUI\b", "tui"), (r"\b(model|LLM|GPT|Claude|Gemini|Llama|Qwen)\b", "models")):
        if len(tags) < 5 and tag not in tags and re.search(pattern, title, re.I):
            tags.append(tag)
    title = f"{source['name']} {title}" if source.get("github_release") else title
    article = {"title": title, "summary": summary, "url": url,
               "source": source["name"], "published_at": candidate["published_at"],
               "category": category, "tags": tags,
               "article_type": article_type}
    return article


def validate(edition, *, allow_seed=False):
    if set(edition) != {"schema_version", "date", "generated_at", "count", "articles"} or edition["schema_version"] not in {1, 2}:
        raise ValueError("invalid top-level contract")
    version = edition["schema_version"]
    categories_allowed = CATEGORIES_V1 if version == 1 else CATEGORIES
    max_articles = 20 if version == 1 else MAX_ARTICLES
    dt.date.fromisoformat(edition["date"])
    generated = dt.datetime.fromisoformat(edition["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo != dt.timezone.utc:
        raise ValueError("generated_at must be UTC")
    articles = edition["articles"]
    if edition["count"] != len(articles) or not (allow_seed and not articles or MIN_ARTICLES <= len(articles) <= max_articles):
        raise ValueError("invalid article count")
    urls, events, domains, categories = set(), set(), {}, {}
    for article in articles:
        if set(article) != {"title", "summary", "url", "source", "published_at", "category", "tags", "article_type"}:
            raise ValueError("invalid article fields")
        url = normalized_url(article["url"])
        event = event_key(article["title"])
        if url in urls or same_event(event, events):
            raise ValueError("duplicate article")
        urls.add(url)
        events.add(event)
        domain = urlparse(url).hostname
        domains[domain] = domains.get(domain, 0) + 1
        categories[article["category"]] = categories.get(article["category"], 0) + 1
        if domains[domain] > MAX_PER_DOMAIN or categories[article["category"]] > max(MAX_PER_CATEGORY, SELECTION_CATEGORY_LIMITS.get(article["category"], MAX_PER_CATEGORY)):
            raise ValueError("source or category cap exceeded")
        if article["category"] not in categories_allowed or article["article_type"] not in TYPES:
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


def recent_archive_keys(now):
    urls, events = set(), set()
    for offset in range(1, 8):
        date = (now.astimezone(ZoneInfo("Europe/Kyiv")).date() - dt.timedelta(days=offset)).isoformat()
        path = ROOT / "news" / "archive" / f"{date}.json"
        if path.exists():
            for article in json.loads(path.read_text())["articles"]:
                urls.add(normalized_url(article["url"]))
                events.add(event_key(article["title"]))
    return urls, events


def build(now=None, *, sources=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    sources = sources or json.loads((ROOT / "config" / "sources.json").read_text())
    sources = [source for source in sources if source.get("category") not in EXCLUDED_CATEGORIES]
    candidates, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(parse_source, source, now): source["name"] for source in sources}
        for future in concurrent.futures.as_completed(futures):
            try:
                candidates.extend(future.result())
            except Exception as exc:
                errors.append(f"{futures[future]}: {type(exc).__name__}: {exc}")
    candidates.sort(key=lambda item: dt.datetime.fromisoformat(item["published_at"]), reverse=True)
    # Give each topic a first chance before a busy feed consumes the budget.
    by_category = {}
    for candidate in candidates:
        by_category.setdefault(candidate["source_config"]["category"], []).append(candidate)
    for bucket in by_category.values():
        bucket.sort(key=lambda candidate: candidate["source_config"].get("priority", 1))
    category_order = sorted(by_category, key=lambda category:
                            PREFERRED_CATEGORIES.index(category) if category in PREFERRED_CATEGORIES else len(PREFERRED_CATEGORIES))
    ordered = [candidate for row in zip_longest(*(by_category[category] for category in category_order))
               for candidate in row if candidate is not None]
    selected, rejected, selected_sources = [], {}, {}
    seen, event_keys = recent_archive_keys(now)
    domain_counts, category_counts = {}, {}
    for candidate in ordered[:200]:
        if len(selected) >= MAX_ARTICLES:
            break
        try:
            article = inspect_candidate(candidate)
            url = article["url"]
            event = event_key(article["title"])
            domain = urlparse(url).hostname
            category = article["category"]
            if category in EXCLUDED_CATEGORIES:
                raise ValueError("excluded category")
            if url in seen or same_event(event, event_keys):
                raise ValueError("duplicate or recent archive")
            if domain_counts.get(domain, 0) >= MAX_PER_DOMAIN or category_counts.get(category, 0) >= SELECTION_CATEGORY_LIMITS.get(category, MAX_PER_CATEGORY):
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
    edition = {"schema_version": 2, "date": date, "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
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
        if edition["count"] < TARGET_ARTICLES:
            print(f"degraded edition: {edition['count']} items", file=sys.stderr)
        publish(edition)
    else:
        path = Path(args.path or ROOT / "news" / "latest.json")
        edition = json.loads(path.read_text())
        validate(edition, allow_seed=edition["count"] == 0)
        print(f"valid count={edition['count']}")


if __name__ == "__main__":
    main()
