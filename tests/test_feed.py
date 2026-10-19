import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feed


def edition(count):
    articles = []
    names = ["Amber", "Birch", "Cedar", "Dahlia", "Elm", "Firwood", "Ginkgo", "Hazel", "Iris", "Juniper"]
    for index in range(count):
        articles.append({"title": f"Distinct {names[index]} project release notes", "summary": "The maintainers describe a specific new release and its changes for developers. The linked technical article explains the release details, compatibility considerations, and practical reasons readers may want to examine the update before adopting it in their own projects.", "url": f"https://source{index // 3}.example/item/{index}", "source": "Example", "published_at": None, "category": ["linux", "programming", "ai"][index // 4], "tags": ["release", "tools"], "article_type": "release"})
    return {"schema_version": 1, "date": "2026-10-20", "generated_at": "2026-10-19T22:00:00Z", "count": count, "articles": articles}


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

    def test_kyiv_daylight_saving_dates(self):
        with patch.object(feed, "parse_source", return_value=[]), patch.object(feed, "recent_urls", return_value=set()):
            for instant, expected in [("2026-03-28T22:30:00+00:00", "2026-03-29"),
                                      ("2026-10-24T21:30:00+00:00", "2026-10-25")]:
                now = dt.datetime.fromisoformat(instant)
                with self.assertRaisesRegex(ValueError, "count"):
                    feed.build(now, sources=[{"name": "test"}])
                self.assertEqual(now.astimezone(feed.ZoneInfo("Europe/Kyiv")).date().isoformat(), expected)

    def test_seed_files_are_identical(self):
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


if __name__ == "__main__":
    unittest.main()
