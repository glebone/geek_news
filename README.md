# Geek News

Public JSON feed for a terminal reader:

`https://raw.githubusercontent.com/glebone/geek_news/main/news/latest.json`

The same bytes are kept at `news/archive/YYYY-MM-DD.json`, where the date is
in `Europe/Kyiv`. The current file contains the most recently published
edition. Check `date` and `generated_at` before treating it as fresh.

## Contract

[`news/schema-v2.json`](news/schema-v2.json) describes the current feed;
[`news/schema-v1.json`](news/schema-v1.json) remains available for older
archives. Each article
has a title, 25–55 word source-authored excerpt or page lead, canonical HTTPS URL, source, optional
publication timestamp, primary category, two to five tags, and article type.
Categories are `linux`, `macos`, `ios`, `android`, `hardware`, `programming`,
`functional_programming`, `retro_computing`, `retro_gaming`, `ai`,
`agent_tools`, `open_source`, `security`, `privacy`, `web_platform`, and
`cloud`. New editions exclude `security` and `privacy`; the schema keeps them
for older archives. The TUI should use `generated_at` to show
freshness, and treat the previous good edition as stale if it is more than a
day old. `count` equals the number of articles.

[`tests/fixtures/example-v1.json`](tests/fixtures/example-v1.json) is a
one-item TUI parsing example, not a production edition.

To build a reader for the public feed, follow
[`docs/build-news-reader.md`](docs/build-news-reader.md). The
[`docs/reader-contract.md`](docs/reader-contract.md) describes fields,
freshness, and failure handling in more detail.

## How it works

`config/sources.json` lists source-operated RSS/Atom feeds. The publisher
checks recent timestamps, resolves each article over HTTPS, compares its
title and excerpt with the destination page, excludes recent archive URLs,
limits domains to four, gives each topic a first chance, and validates the
finished JSON. The source mix favors BSD and Haiku, Linux gaming and software,
programming, AI models and coding tools, and TUI projects. Privacy and security
topics are excluded from new editions. It targets 25–30 items. At least 10
are required; a 10–24 item run is logged as degraded. Fewer than 10 items
leaves the previous public feed
unchanged. The summaries are **extractive source text**, not model-written
or human-curated copy. No LLM API is used.

The workflow starts at 03:07 `Europe/Kyiv` to aim for a feed ready by 07:00,
with retries at 04:07, 05:07, 06:07, 06:42, 07:07, 07:22, 07:37, and 07:52
until today's edition has at least 25 stories. A scheduled retry keeps the
larger same-day edition if fewer stories pass. Code push and manual dispatch are also
available. GitHub Actions `contents: write` is
scoped to the publisher job. A successful edition is committed in one commit
and verified against the public raw URL. GitHub may delay or skip a scheduled run or
raw-content propagation. The separate freshness workflow checks the public
feed at 08:30 Kyiv time and reports a failure when today's edition is missing
or the latest and archive files differ.

## Development

Python 3.12 and its standard library are sufficient:

```sh
cd /workspace/geek_news
python3 -m unittest discover -s tests -v
python3 scripts/feed.py validate
python3 scripts/feed.py build
```

The last command needs access to the configured source sites and will exit
without replacing the seed or previous good edition if too few sources pass.
Each cloud task already has an isolated environment; use its existing checkout
and do not create a Git worktree for this workflow unless requested.
