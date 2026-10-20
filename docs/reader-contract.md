# Geek News reader contract

## Public files

The reader needs no token or GitHub API access. Fetch these HTTPS URLs:

| Purpose | URL |
| --- | --- |
| Most recently published edition | `https://raw.githubusercontent.com/glebone/geek_news/main/news/latest.json` |
| Edition for a Kyiv calendar day | `https://raw.githubusercontent.com/glebone/geek_news/main/news/archive/YYYY-MM-DD.json` |
| Current JSON Schema | `https://raw.githubusercontent.com/glebone/geek_news/main/news/schema-v2.json` |
| Legacy JSON Schema | `https://raw.githubusercontent.com/glebone/geek_news/main/news/schema-v1.json` |

Replace `YYYY-MM-DD` with a date calculated in `Europe/Kyiv`, not the user's
machine timezone. `latest.json` and its matching archive file are published
with identical bytes, although the public CDN can briefly serve different
revisions of the two URLs after a commit. HTTP `404` for a future or unpublished
archive is normal. GitHub raw responses can also be cached for a few minutes.

## Version 2 JSON

[`news/schema-v2.json`](../news/schema-v2.json) is the current field
definition. A real edition has this shape:

```json
{
  "schema_version": 2,
  "date": "2026-10-20",
  "generated_at": "2026-10-20T04:00:00Z",
  "count": 20,
  "articles": [
    {
      "title": "Example headline",
      "summary": "A source excerpt of roughly 25 to 55 words belongs here. The publisher uses source text for these summaries, so the reader should display the supplied copy without suggesting that it was written by a human editor or a language model.",
      "url": "https://example.org/story",
      "source": "Example source",
      "published_at": "2026-10-20T01:15:00Z",
      "category": "programming",
      "tags": ["example", "development"],
      "article_type": "news"
    }
  ]
}
```

The object above illustrates fields; its `count` and sample article are not a
valid full edition. Use [`tests/fixtures/example-v1.json`](../tests/fixtures/example-v1.json)
for a one-item parser fixture and the public feed for a production edition.

- `schema_version` is `2` for new editions. Older archives may be version `1`;
  their field names are the same, but they allow at most 20 articles and do not
  use the four new categories. Reject unknown versions.
- `date` is the edition's `Europe/Kyiv` calendar date. `generated_at` is an
  ISO 8601 timestamp with a UTC offset. `count` equals `len(articles)`.
- Each article has `title`, `summary`, `url`, `source`, `published_at`,
  `category`, `tags`, and `article_type`. `published_at` may be `null`.
- Categories: `linux`, `macos`, `ios`, `android`, `hardware`, `programming`,
  `functional_programming`, `retro_computing`, `retro_gaming`, `ai`,
  `agent_tools`, `open_source`, `security`, `privacy`, `web_platform`, `cloud`.
- Article types: `news`, `release`, `technical_article`, `research`, `rumor`,
  `project`. A `rumor` should be visibly labeled as such.
- Version 2 production editions contain 10–25 articles. A 10–19 article
  edition is a degraded day; 20–25 is the target. If fewer than 10 pass validation,
  the publisher preserves the previous good `latest.json`.
- Tags such as `openbsd`, `netbsd`, `haiku`, `linux-gaming`, `steam`, `tui`, and
  `models` can drive interest filters in a reader. The publisher gives more
  slots to those areas and limits privacy and security to one item each.

## Freshness and failures

The intended publication time is 07:00 Kyiv time. Before that time, yesterday's
edition can be the newest available; after that time, today's archive can still
appear late if GitHub delays a scheduled job or public cache propagation. A
reader should use the JSON `date`, not the HTTP `Last-Modified` header, to tell
which edition it is showing.

1. Fetch `latest.json` with a finite timeout and a response size limit.
2. Check the version, date, article count, required fields, and article URLs.
3. If `date` is today in Kyiv, show **Today's news** and the `generated_at`
   timestamp. Mark 10–19 articles as a shorter edition if useful.
4. If `latest.json` is older or unavailable, try today's archive URL. If it is
   valid, show that edition. Otherwise, a valid older edition may be shown with
   an unmistakable **Previous edition — DATE** label. Never silently present
   yesterday's news as today's.
5. On network or parse failure, keep a previously validated local copy if the
   app has one. Label its date and offline state. Do not invent empty articles.

Treat article fields as untrusted display text: strip terminal control
characters and escape any markup your UI framework supports. Open only HTTPS
article URLs. Avoid using a live article title or URL as a shell command.
