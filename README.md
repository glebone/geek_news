# Geek News

Public JSON feed for a terminal reader:

`https://raw.githubusercontent.com/glebone/geek_news/main/news/latest.json`

The same bytes are kept at `news/archive/YYYY-MM-DD.json`, where the date is
in `Europe/Kyiv`. The current file contains the most recently published
edition. Check `date` and `generated_at` before treating it as fresh.

## Contract

[`news/schema-v1.json`](news/schema-v1.json) describes version 1. Each article
has a title, 25–55 word source-authored excerpt or page lead, canonical HTTPS URL, source, optional
publication timestamp, primary category, two to five tags, and article type.
Categories are `linux`, `macos`, `ios`, `android`, `hardware`, `programming`,
`functional_programming`, `retro_computing`, `retro_gaming`, `ai`,
`agent_tools`, and `open_source`. The TUI should use `generated_at` to show
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
limits domains to three and categories to four, and validates the finished
JSON. It selects up to 20 items. At least 10 are required; a 10–14 item run
is logged as degraded. Fewer than 10 items leaves the previous public feed
unchanged. The summaries are **extractive source text**, not model-written
or human-curated copy. No LLM API is used.

The workflow currently runs on a code push or manual dispatch for testing.
The daily schedule is deliberately pending an actual successful unattended
trial. GitHub Actions `contents: write` is scoped to the publisher job. A
successful edition is committed in one commit and verified against the public
raw URL. GitHub may delay raw-content propagation.

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
