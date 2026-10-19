"""Fail if the public TUI feed is stale or its archive differs."""

import datetime as dt
import json
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from feed import validate

BASE = "https://raw.githubusercontent.com/glebone/geek_news/main/news/"


def read(path):
    request = Request(BASE + path, headers={"User-Agent": "GeekNewsFeed/1.0"})
    with urlopen(request, timeout=20) as response:
        return response.read()


def check(now=None, reader=read):
    now = now or dt.datetime.now(dt.timezone.utc)
    expected = now.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
    latest = reader("latest.json")
    edition = json.loads(latest)
    validate(edition)
    if edition["date"] != expected:
        raise ValueError(f"stale edition: expected {expected}, got {edition['date']}")
    archive = reader(f"archive/{expected}.json")
    if archive != latest:
        raise ValueError("public latest and archive bytes differ")
    print(f"fresh edition {expected}: {edition['count']} items; public files match")


if __name__ == "__main__":
    check()
