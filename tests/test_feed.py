import datetime as dt
import email.utils
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feed
import check_freshness


def edition(count, version=2):
    articles = []
    categories = ["linux", "programming", "ai", "hardware", "security", "web_platform", "retro_computing"]
    for index in range(count):
        articles.append({"title": f"Distinct project {index} release notes", "summary": "The maintainers describe a specific new release and its changes for developers. The linked technical article explains the release details, compatibility considerations, and practical reasons readers may want to examine the update before adopting it in their own projects.", "url": f"https://source{index // 3}.example/item/{index}", "source": "Example", "published_at": None, "category": categories[index // 4], "tags": ["release", "tools"], "article_type": "release"})
    return {"schema_version": version, "date": "2026-10-20", "generated_at": "2026-10-19T22:00:00Z", "count": count, "articles": articles}


class FeedTests(unittest.TestCase):
    def test_valid_and_duplicate_urls(self):
        item = edition(10)
        self.assertTrue(feed.validate(item))
        item["articles"][1]["url"] = item["articles"][0]["url"] + "?utm_source=test"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            feed.validate(item)

    def test_low_count_and_unqualified_rumor(self):
        with self.assertRaisesRegex(ValueError, "count"):
            feed.validate(edition(9))
        item = edition(10)
        item["articles"][0]["article_type"] = "rumor"
        with self.assertRaisesRegex(ValueError, "rumor"):
            feed.validate(item)

    def test_v2_expanded_count_and_v1_compatibility(self):
        self.assertTrue(feed.validate(edition(25)))
        with self.assertRaisesRegex(ValueError, "count"):
            feed.validate(edition(26))
        self.assertTrue(feed.validate(edition(10, version=1)))
        with self.assertRaisesRegex(ValueError, "count"):
            feed.validate(edition(21, version=1))
        legacy = edition(10, version=1)
        legacy["articles"][0]["category"] = "security"
        with self.assertRaisesRegex(ValueError, "category"):
            feed.validate(legacy)

    def test_build_gives_topics_a_first_chance(self):
        now = dt.datetime(2026, 10, 20, 5, tzinfo=dt.timezone.utc)
        sample = edition(25)
        candidates = []
        for index, article in enumerate(sample["articles"]):
            source = {"name": f"source-{index}", "category": article["category"]}
            candidates.append({"source_config": source, "published_at": (now - dt.timedelta(minutes=index)).isoformat(), "article": article})
        with patch.object(feed, "parse_source", return_value=candidates), \
             patch.object(feed, "inspect_candidate", side_effect=lambda candidate: candidate["article"]), \
             patch.object(feed, "recent_archive_keys", return_value=(set(), set())):
            result = feed.build(now, sources=[{"name": "test"}])
        self.assertEqual(result["count"], 25)
        self.assertEqual(len({article["category"] for article in result["articles"][:7]}), 7)

    def test_kyiv_daylight_saving_dates(self):
        with patch.object(feed, "parse_source", return_value=[]), patch.object(feed, "recent_archive_keys", return_value=(set(), set())):
            for instant, expected in [("2026-03-28T22:30:00+00:00", "2026-03-29"),
                                      ("2026-10-24T21:30:00+00:00", "2026-10-25")]:
                now = dt.datetime.fromisoformat(instant)
                with self.assertRaisesRegex(ValueError, "count"):
                    feed.build(now, sources=[{"name": "test"}])
                self.assertEqual(now.astimezone(feed.ZoneInfo("Europe/Kyiv")).date().isoformat(), expected)

    def test_latest_and_archive_files_are_identical(self):
        root = Path(__file__).resolve().parents[1]
        latest = (root / "news/latest.json").read_bytes()
        item = json.loads(latest)
        self.assertEqual(latest, (root / "news/archive" / f"{item['date']}.json").read_bytes())
        self.assertTrue(feed.validate(item, allow_seed=True))

    def test_article_page_lead_when_feed_excerpt_is_short(self):
        page = '<html><head><meta name="description" content="The maintainers announced a new release with several changes for developers. This source page explains the compatibility details, the updated tooling, and how the project team expects users to evaluate the release before using it in production deployments today."></head></html>'
        summary = feed.summarize("A short release note", page)
        self.assertGreaterEqual(len(summary.split()), 25)
        self.assertLessEqual(len(summary.split()), 55)

    def test_summary_rejects_affiliate_copy(self):
        page = '<article><p>This is an eBay paid affiliate link and a discount offer for readers who want to purchase products from our partner storefront today and support the website through their orders.</p></article>'
        with self.assertRaisesRegex(ValueError, "relevant"):
            feed.summarize("short", page, "Atari Jaguar restoration")

    def test_summary_rejects_incomplete_sentence(self):
        partial = "The team announced a new release with a long description of features and updates for developers who use the project every day, including a change to the"
        with self.assertRaisesRegex(ValueError, "relevant"):
            feed.summarize(partial, "", "new release")

    def test_archive_source_excludes_unrelated_events(self):
        now = dt.datetime(2026, 10, 20, 5, tzinfo=dt.timezone.utc)
        published = email.utils.format_datetime(now - dt.timedelta(hours=1))
        xml = f"""<rss><channel>
          <item><title>Annual celebration street fair</title><link>https://blog.archive.org/fair</link><pubDate>{published}</pubDate></item>
          <item><title>Classic computer preservation update</title><link>https://blog.archive.org/computers</link><pubDate>{published}</pubDate></item>
        </channel></rss>""".encode()
        source = {"name": "Internet Archive", "url": "https://blog.archive.org/feed/", "title_keywords": ["computer", "emulator", "preservation"]}
        with patch.object(feed, "fetch", return_value=(source["url"], xml, "application/rss+xml")):
            articles = feed.parse_source(source, now)
        self.assertEqual([article["title"] for article in articles], ["Classic computer preservation update"])

    def test_similar_event_titles_deduplicate_but_versions_do_not(self):
        old = feed.event_key("Rust 1.93 release brings faster builds")
        self.assertTrue(feed.same_event(feed.event_key("Rust 1.93 release brings faster compiler builds"), {old}))
        self.assertFalse(feed.same_event(feed.event_key("Rust 1.94 release brings faster builds"), {old}))
        privacy = feed.event_key("Announcing Privacy Badger for Safari on macOS")
        self.assertTrue(feed.same_event(feed.event_key("EFF Launches Privacy Badger for Safari on macOS"), {privacy}))

    def test_public_freshness_rejects_stale_date(self):
        item = edition(10)
        data = json.dumps(item).encode()
        now = dt.datetime(2026, 10, 20, 12, tzinfo=dt.timezone.utc)
        check_freshness.check(now, reader=lambda path: data)
        with self.assertRaisesRegex(ValueError, "stale edition"):
            check_freshness.check(now + dt.timedelta(days=1), reader=lambda path: data)


if __name__ == "__main__":
    unittest.main()
