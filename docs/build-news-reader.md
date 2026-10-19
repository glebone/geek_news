# Build a terminal Geek News reader

This guide builds a small Python terminal reader for today's public Geek News
edition. It uses Python 3.12 and the standard library. The feed requires no
API key. Read [the feed contract](reader-contract.md) before adding filters,
caching, or another UI framework.

## 1. Create a minimal reader

Save this as `reader.py` in your reader project. It fetches the public GitHub
file, checks its core fields, falls back to today's dated archive when needed,
and clearly labels an older edition. Numbered article links open in the
default browser.

```python
import datetime as dt
import json
import sys
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from zoneinfo import ZoneInfo

BASE = "https://raw.githubusercontent.com/glebone/geek_news/main/news/"
KYIV = ZoneInfo("Europe/Kyiv")
MAX_BYTES = 1_000_000


def safe_text(value):
    """Keep source text from controlling the terminal display."""
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))


def fetch(path):
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "GeekNewsReader/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("feed exceeds size limit")
    edition = json.loads(data)
    if not isinstance(edition, dict):
        raise ValueError("feed root must be an object")
    version = edition.get("schema_version")
    if version not in (1, 2):
        raise ValueError("unsupported feed version")
    dt.date.fromisoformat(edition["date"])
    generated = dt.datetime.fromisoformat(edition["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("missing publication timezone")
    articles = edition["articles"]
    if not isinstance(articles, list) or edition["count"] != len(articles):
        raise ValueError("article count mismatch")
    if not 10 <= len(articles) <= (20 if version == 1 else 25):
        raise ValueError("unexpected article count")
    for item in articles:
        if not isinstance(item, dict):
            raise ValueError("article must be an object")
        if not all(isinstance(item.get(key), str) for key in
                   ("title", "summary", "source", "category", "article_type")):
            raise ValueError("missing article text")
        url = item.get("url")
        if not isinstance(url, str) or any(ord(c) < 32 for c in url):
            raise ValueError("unsafe article URL")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("unsafe article URL")
        if not isinstance(item.get("tags"), list):
            raise ValueError("missing article tags")
    return edition


def main():
    today = dt.datetime.now(KYIV).date().isoformat()
    errors = []
    latest = None
    try:
        latest = fetch("latest.json")
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        errors.append(f"latest.json: {exc}")
    if latest and latest["date"] > today:
        errors.append("latest.json: future edition date")
        latest = None

    edition = latest if latest and latest["date"] == today else None
    if edition is None:
        try:
            archive = fetch(f"archive/{today}.json")
            if archive["date"] != today:
                raise ValueError("archive date mismatch")
            edition = archive
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            errors.append(f"today's archive: {exc}")
            edition = latest

    if edition is None:
        print("No valid news edition is available.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    status = "Today's news" if edition["date"] == today else "Previous edition"
    print(f"{status} — {edition['date']} ({edition['count']} articles)")
    print(f"Published: {edition['generated_at']}")
    if edition["date"] != today:
        print("Today's edition is not available yet; these are older stories.")
    elif edition["count"] < 20 and edition["schema_version"] == 2:
        print("Shorter edition today: fewer articles passed the publisher checks.")
    print()

    for number, item in enumerate(edition["articles"], 1):
        title = safe_text(item["title"])
        source = safe_text(item["source"])
        kind = safe_text(item["article_type"])
        print(f"{number:2}. {title}  [{source} · {kind}]")
        print(f"    {safe_text(item['summary'])}")
        print()

    try:
        choice = input("Article number to open (Enter to quit): ").strip()
    except EOFError:
        return 0
    if choice:
        if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(edition["articles"]):
            print("Invalid article number.", file=sys.stderr)
            return 1
        webbrowser.open(edition["articles"][int(choice) - 1]["url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it with `python3 reader.py`. On Windows, install the `tzdata` Python
package if your Python distribution lacks the `Europe/Kyiv` time zone data.
The browser opening step is optional; a pure TUI can show the selected URL
instead.

## 2. Turn the minimal reader into a TUI

Keep fetching and parsing separate from drawing the screen. A simple layout
has a header with the edition date and freshness label, a scrollable story
list, and a detail pane for the selected summary. Show the source and
`article_type` beside each title; label `rumor` items clearly. Useful keys are
Up/Down or `j`/`k` to move, Enter to open the HTTPS article URL, `/` to search
titles and summaries, `c` to filter by category, `r` to refetch, and `q` to
quit. Keep the active filter when the feed refreshes.

Use the [`category` and `tags` fields](reader-contract.md#version-1-json) for
filters. Preserve publication order unless the user chooses a sort. Show
`generated_at` in local time only after parsing its UTC offset; retain the
edition's Kyiv `date` in the freshness label.

## 3. Add caching and checks

- Cache only a fully validated edition, written atomically to a user data
  directory. On a network failure, show that cached edition with its actual
  date and an offline label. Limit cache size and never use cache contents as
  executable code.
- Put a 15-second timeout and a response size cap on each download. Retry a
  temporary network error once if desired, with a short delay. A CDN may briefly
  serve an older `latest.json` while today's archive is already available.
- Test individual field parsing with
  [`tests/fixtures/example-v1.json`](../tests/fixtures/example-v1.json); its
  one item is deliberately below the reader's production minimum. Test the
  running reader against the public `latest.json` URL. Exercise an
  unknown `schema_version`, missing fields, invalid JSON, unsafe URL, HTTP
  failure, yesterday's edition, a 10–19 article edition, and both supported
  schema versions.
- For a live check after the intended 07:00 Kyiv publication time, compare the
  reader's displayed date with `python3 scripts/check_freshness.py` from this
  repository. That script also checks that the public latest and archive bytes
  match once the CDN has converged.
