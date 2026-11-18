"""Polaris Meme поверх настоящего StorageCore: загрузка, сборка оригинала из чанков, дубли, правка, удаление, сбои."""
from __future__ import annotations

import hashlib
import types
from unittest import mock

from polaris.modules.meme import media, repository as repo
from polaris.modules.meme.service import BUCKET, MemeError, MemeService
from polaris.modules.storage import download_links
from polaris.storage.errors import StorageBusy, StorageUnavailable
from tests.meme_helpers import BOT_TOKEN, MemeTestCase, gif, jpeg, png, webp


class RoundTripTests(MemeTestCase):
    def test_original_is_reassembled_byte_for_byte_from_many_chunks(self):
        source = png(60, 40, seed=3)
        created = self.service.create(source, filename="cat.png", title="Спокойной ночи с котиком",
                                      tags=["спокойной ночи", "котик", "сон", "милота"])
        self.assertGreater(len(self.driver.blobs), 5, "картинка должна лежать в Storage отдельными чанками")
        self.assertTrue(all(len(b) <= self.chunk_size for b in self.driver.blobs.values()))
        row, data = self.service.read_original(created["id"])
        self.assertEqual(data, source)  # цельный файл, а не части
        self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
        self.assertEqual(row["mime_type"], "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_object_lands_in_storage_under_meme_bucket_with_extension_and_mime(self):
        for data, ext, mime in ((png(seed=1), ".png", "image/png"), (jpeg(seed=1), ".jpg", "image/jpeg"),
                                (gif(seed=1), ".gif", "image/gif"), (webp(seed=1), ".webp", "image/webp")):
            created = self.service.create(data, filename="x", title=f"t{ext}")
            obj = self.core.get_metadata_by_key(BUCKET, f"memes/{created['id']}{ext}")
            self.assertEqual(obj.mime_type, mime)
            self.assertEqual(obj.size_bytes, len(data))

    def test_public_record_does_not_leak_storage_internals(self):
        created = self.service.create(png(), filename="a.png", title="A")
        row = repo.get(created["id"])
        flat = repr(created)
        for secret in (row["storage_object_id"], BUCKET, "tg_file_id", ".part", "polaris-storage:v1", "blob="):
            self.assertNotIn(secret, flat)
        self.assertTrue(created["image_url"].startswith("/api/meme/media/"))
        self.assertNotIn(row["storage_object_id"], created["image_url"])

    def test_title_defaults_to_filename_and_metadata_is_normalised(self):
        created = self.service.create(png(seed=9), filename="good_night_cat.png", title="  ",
                                      tags="#Котик, котик , сон,\nсон", description="  милый  ")
        self.assertEqual(created["title"], "good night cat")
        self.assertEqual(created["tags"], ["Котик", "сон"])
        self.assertEqual(created["description"], "милый")

    def test_metadata_validation(self):
        def create(**kw):
            return self.service.create(png(seed=len(repr(kw))), filename="a.png", **{"title": "T", **kw})
        for kw in ({"title": "x" * 121}, {"description": "x" * 501}, {"tags": ["x" * 41]}, {"tags": [str(i) for i in range(21)]}):
            with self.assertRaises(MemeError) as ctx:
                create(**kw)
            self.assertEqual(ctx.exception.status, 422, kw)
        with self.assertRaises(MemeError):
            self.service.create(png(seed=77), title="", filename="")
        self.assertEqual(self.stored_objects(), [])


class DuplicateAndInvalidTests(MemeTestCase):
    def test_same_image_is_not_added_twice(self):
        first = self.service.create(png(seed=5), filename="a.png", title="Первый")
        uploads = self.driver.upload_count
        with self.assertRaises(MemeError) as ctx:
            self.service.create(png(seed=5), filename="copy.png", title="Копия")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.extra["existing_id"], first["id"])
        self.assertIn("Первый", ctx.exception.detail)
        self.assertEqual(self.driver.upload_count, uploads, "дубль не должен уходить в Storage")
        self.assertEqual(len(self.stored_objects()), 1)
        self.assertEqual(repo.count(), 1)

    def test_race_between_identical_uploads_leaves_no_orphan_object(self):
        self.service.create(png(seed=6), filename="a.png", title="Первый")
        # обе загрузки прошли проверку «такого ещё нет» и дошли до записи в БД: вторую остановит UNIQUE
        with mock.patch.object(repo, "get_by_sha", side_effect=[None, repo.get_by_sha(hashlib.sha256(png(seed=6)).hexdigest())]):
            with self.assertRaises(MemeError) as ctx:
                self.service.create(png(seed=6), filename="b.png", title="Вторая")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(self.stored_objects()), 1, "лишний объект Storage должен быть убран")

    def test_invalid_files_never_reach_storage_or_db(self):
        bad = {
            "текст": b"hello world",
            "svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
            "обрезанный png": png()[:-20],
            "анимированный webp": webp(animated=True),
            "пустой": b"",
        }
        for name, data in bad.items():
            with self.assertRaises(MemeError, msg=name) as ctx:
                self.service.create(data, filename="x.png", title="T")
            self.assertEqual(ctx.exception.status, 422, name)
        with mock.patch.object(media, "MAX_BYTES", 50):
            with self.assertRaises(MemeError) as ctx:
                self.service.create(png(), filename="big.png", title="T")
            self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(self.driver.upload_count, 0)
        self.assertEqual(repo.count(), 0)


class UpdateDeleteTests(MemeTestCase):
    def test_update_changes_search_results(self):
        created = self.service.create(png(seed=1), filename="a.png", title="Старое имя", tags=["старый"])
        updated = self.service.update(created["id"], {"title": "Новое имя", "tags": ["новый", "тег"], "description": "про котов"})
        self.assertEqual((updated["title"], updated["tags"], updated["description"]), ("Новое имя", ["новый", "тег"], "про котов"))
        titles = lambda q: [i["title"] for i in self.service.listing(q)["items"]]  # noqa: E731
        self.assertEqual(titles("новый"), ["Новое имя"])
        self.assertEqual(titles("котов"), ["Новое имя"])
        self.assertEqual(titles("старый"), [])
        self.assertEqual(titles("старое"), [])

    def test_partial_update_keeps_other_fields_and_rejects_empty(self):
        created = self.service.create(png(seed=2), filename="a.png", title="Имя", tags=["а"], description="д")
        self.assertEqual(self.service.update(created["id"], {"tags": []})["title"], "Имя")
        for bad in (None, {}, {"title": ""}, []):
            with self.assertRaises(MemeError):
                self.service.update(created["id"], bad)
        with self.assertRaises(MemeError) as ctx:
            self.service.update("нет-такого", {"title": "x"})
        self.assertEqual(ctx.exception.status, 404)

    def test_delete_removes_record_and_storage_object(self):
        created = self.service.create(png(seed=3), filename="a.png", title="A")
        self.service.delete(created["id"])
        self.assertEqual(self.stored_objects(), [])
        self.assertIsNone(repo.get(created["id"]))
        self.assertEqual(self.driver.blobs, {}, "чанки должны быть удалены физически")
        with self.assertRaises(MemeError) as ctx:
            self.service.get(created["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_delete_works_when_file_was_already_removed_from_storage(self):
        created = self.service.create(png(seed=4), filename="a.png", title="A")
        self.core.delete_object(repo.get(created["id"])["storage_object_id"])  # убрали через раздел Files
        self.service.delete(created["id"])
        self.assertEqual(repo.count(), 0)

    def test_record_is_kept_when_storage_is_down_during_delete(self):
        created = self.service.create(png(seed=5), filename="a.png", title="A")
        self.service._get_service = lambda: None
        with self.assertRaises(MemeError) as ctx:
            self.service.delete(created["id"])
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(repo.count(), 1)


class StorageFailureTests(MemeTestCase):
    def test_storage_not_running_or_unavailable_gives_503_and_writes_nothing(self):
        broken = types.SimpleNamespace(get_core=lambda: (_ for _ in ()).throw(StorageUnavailable("выключен")))
        for getter in (lambda: None, lambda: broken):
            service = MemeService(self.settings, get_service=getter)
            with self.assertRaises(MemeError) as ctx:
                service.create(png(), filename="a.png", title="A")
            self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(repo.count(), 0)

    def test_busy_storage_is_503_without_internals_in_message(self):
        with mock.patch.object(self.core, "put_object", side_effect=StorageBusy("слоты: секретная деталь backend")):
            with self.assertRaises(MemeError) as ctx:
                self.service.create(png(), filename="a.png", title="A")
        self.assertEqual(ctx.exception.status, 503)
        self.assertNotIn("секретная", ctx.exception.detail)
        self.assertEqual(repo.count(), 0)

    def test_database_failure_after_upload_cleans_up_storage(self):
        with mock.patch.object(repo, "insert", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.service.create(png(seed=8), filename="a.png", title="A")
        self.assertEqual(self.stored_objects(), [])

    def test_corrupted_chunk_is_never_served(self):
        created = self.service.create(png(seed=10), filename="a.png", title="A")
        ref = next(iter(self.driver.blobs))
        self.driver.blobs[ref] = bytes(len(self.driver.blobs[ref]))  # тот же размер, другое содержимое
        with self.assertRaises(MemeError) as ctx:
            self.service.read_original(created["id"])
        self.assertEqual(ctx.exception.status, 502)

    def test_record_without_file_in_storage_is_404_not_500(self):
        created = self.service.create(png(seed=11), filename="a.png", title="A")
        self.core.delete_object(repo.get(created["id"])["storage_object_id"])
        with self.assertRaises(MemeError) as ctx:
            self.service.read_original(created["id"])
        self.assertEqual(ctx.exception.status, 404)


class SignedLinkTests(MemeTestCase):
    def setUp(self):
        super().setUp()
        self.created = self.service.create(png(seed=12), filename="a.png", title="A")
        self.token = self.created["image_url"].rsplit("/", 1)[-1]

    def test_valid_token_returns_the_image(self):
        row, data = self.service.read_by_token(self.token)
        self.assertEqual(row["id"], self.created["id"])
        self.assertEqual(data, png(seed=12))

    def test_forged_expired_and_foreign_tokens_are_refused(self):
        obj_id = repo.get(self.created["id"])["storage_object_id"]
        tokens = {
            "мусор": "abc.def",
            "подделка": self.token[:-3] + ("AAA" if not self.token.endswith("AAA") else "BBB"),
            "чужой секрет": download_links.sign("another-secret", "meme:" + self.created["id"]),
            "просрочен": download_links.sign(BOT_TOKEN, "meme:" + self.created["id"], ttl=-5),
            "токен Storage на объект": download_links.sign(BOT_TOKEN, obj_id),
            "id без префикса": download_links.sign(BOT_TOKEN, self.created["id"]),
        }
        for label, token in tokens.items():
            with self.assertRaises(MemeError, msg=label) as ctx:
                self.service.read_by_token(token)
            self.assertEqual(ctx.exception.status, 403, label)

    def test_meme_token_does_not_open_storage_download_route(self):
        subject = download_links.verify(BOT_TOKEN, self.token)
        self.assertTrue(subject.startswith("meme:"))
        # у Storage-маршрута /api/storage/download ищется объект с таким id — объекта «meme:<uuid>» там нет
        with self.assertRaises(Exception):
            self.core.get_metadata(subject)

    def test_no_secret_configured_is_503(self):
        service = MemeService(types.SimpleNamespace(telegram_bot_token="", update_api_token=""),  # type: ignore[arg-type]
                              get_service=lambda: self.storage)
        with self.assertRaises(MemeError) as ctx:
            service.read_by_token(self.token)
        self.assertEqual(ctx.exception.status, 503)


class ListingTests(MemeTestCase):
    def test_pagination_fields(self):
        for i in range(5):
            self.service.create(png(seed=20 + i), filename=f"{i}.png", title=f"Мем {i}", tags=["общий"])
        first = self.service.listing("общий", limit=2, offset=0)
        self.assertEqual((len(first["items"]), first["total"], first["next_offset"]), (2, 5, 2))
        last = self.service.listing("общий", limit=2, offset=4)
        self.assertEqual((len(last["items"]), last["next_offset"]), (1, None))
        self.assertEqual(self.service.listing("общий", limit=999)["limit"], 100)
        self.assertEqual(first["tags"][0]["tag"], "общий")
