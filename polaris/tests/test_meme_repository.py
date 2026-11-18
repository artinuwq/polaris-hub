"""Polaris Meme: поиск по названию, тегам и описанию (кириллица, регистр, ё), ранжирование, пагинация, фильтр по тегу."""
from __future__ import annotations

import sqlite3

from polaris.modules.meme import repository as repo
from tests.meme_helpers import MemeTestCase


class SearchTests(MemeTestCase):
    def add(self, title, tags=(), description="", sha=None):
        sha = sha or (title + "|".join(tags) + description).encode().hex().ljust(64, "0")[:64]
        return repo.insert({
            "id": repo.new_id(), "title": title, "description": description, "tags": list(tags),
            "storage_bucket": "polaris-meme", "storage_object_id": "obj-" + sha[:8], "filename": "f.png",
            "mime_type": "image/png", "size_bytes": 10, "width": 4, "height": 3, "sha256": sha,
        })

    def titles(self, *args, **kwargs):
        return [r["title"] for r in repo.search(*args, **kwargs)[0]]

    def test_spec_example_is_found_by_title_tags_and_description(self):
        self.add("Спокойной ночи с котиком", ["спокойной ночи", "котик", "сон", "милота"], "Милый кот желает добрых снов")
        self.assertEqual(self.titles("спокойной ночи"), ["Спокойной ночи с котиком"])  # название и тег
        self.assertEqual(self.titles("милота"), ["Спокойной ночи с котиком"])           # тег
        self.assertEqual(self.titles("добрых снов"), ["Спокойной ночи с котиком"])      # описание
        self.assertEqual(self.titles("собака"), [])

    def test_case_insensitive_for_cyrillic_and_yo(self):
        self.add("Ёжик в тумане", ["Ёж"])
        for query in ("ЁЖИК", "ежик", "ЕЖ", "ёжик"):
            self.assertEqual(self.titles(query), ["Ёжик в тумане"], query)

    def test_all_words_must_match_in_any_field(self):
        self.add("Кот", ["ночь"], "спит")
        self.add("Кот с едой", ["еда"])
        self.assertEqual(self.titles("кот ночь"), ["Кот"])
        self.assertEqual(self.titles("кот спит"), ["Кот"])
        self.assertEqual(sorted(self.titles("кот")), ["Кот", "Кот с едой"])

    def test_title_match_ranks_above_tag_above_description(self):
        self.add("Про сон", [], "картинка про котов")
        self.add("Тег", ["кот"])
        self.add("Кот на крыше")
        self.assertEqual(self.titles("кот"), ["Кот на крыше", "Тег", "Про сон"])

    def test_like_wildcards_are_literal(self):
        self.add("a_b")
        self.add("axb")
        self.assertEqual(self.titles("a_b"), ["a_b"])  # «_» — не «любой символ»
        self.add("100% успех")
        self.assertEqual(sorted(self.titles("%")), ["100% успех", "a_b", "axb"])  # в запросе нет слов — это пустой запрос

    def test_empty_query_returns_everything_newest_first(self):
        self.add("Первая")
        self.add("Вторая")
        self.assertEqual(self.titles(""), ["Вторая", "Первая"])

    def test_pagination_covers_every_result_once(self):
        for i in range(25):
            self.add(f"Мем {i:02d}", ["общий"])
        seen, offset = [], 0
        while True:
            rows, total = repo.search("общий", "", 10, offset)
            self.assertEqual(total, 25)
            if not rows:
                break
            seen += [r["id"] for r in rows]
            offset += len(rows)
        self.assertEqual(len(seen), 25)
        self.assertEqual(len(set(seen)), 25)

    def test_tag_filter_matches_whole_tag_only(self):
        self.add("А", ["кот"])
        self.add("Б", ["котик"])
        self.assertEqual(self.titles("", "кот"), ["А"])
        self.assertEqual(self.titles("", "КОТИК"), ["Б"])

    def test_top_tags_counts_case_insensitively(self):
        self.add("А", ["Кот", "сон"])
        self.add("Б", ["кот"])
        tags = repo.top_tags()
        self.assertEqual(tags[0]["count"], 2)
        self.assertEqual(tags[0]["tag"].casefold(), "кот")

    def test_duplicate_hash_is_rejected_by_database(self):
        first = self.add("А", sha="a" * 64)
        with self.assertRaises(repo.DuplicateMeme) as ctx:
            self.add("Б", sha="a" * 64)
        self.assertEqual(ctx.exception.existing["id"], first["id"])
        self.assertEqual(repo.count(), 1)
