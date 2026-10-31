"""A menu object that is not valid JSON counts as missing: BROKEN, so ./kopi reset can still run. If it
crashed instead, the state check would fail first and block the command that fixes it."""

import datetime
import io
import unittest
from unittest import mock

from kiosk import s3_store

KEY = "menus/stall-kopi.json"


class FakeS3:
    def get_object(self, **request):
        return {"Body": io.BytesIO(b"not json")}

    def list_object_versions(self, **request):
        return {
            "Versions": [{"Key": KEY, "VersionId": "v1", "Size": 8, "IsLatest": True, "LastModified": mock.MagicMock()}]
        }


def store():
    with mock.patch.object(s3_store.boto3, "client", return_value=FakeS3()):
        return s3_store.MenuStore("bucket", "ap-southeast-2")


class UnreadableMenu(unittest.TestCase):
    def test_the_current_menu_reads_as_missing(self):
        self.assertEqual(store().read_menus({"stalls": {"kopi": {}}}), {KEY: None})

    def test_an_old_version_reads_as_having_no_menu(self):
        self.assertIsNone(store().recent_versions(KEY, 1)[0]["menu"])


class MenuShape(unittest.TestCase):
    def store_returning(self, body):
        fake = mock.Mock()
        fake.get_object.return_value = {"Body": io.BytesIO(body)}
        with mock.patch.object(s3_store.boto3, "client", return_value=fake):
            return s3_store.MenuStore("bucket", "ap-southeast-2")

    def test_valid_json_that_is_not_a_menu_reads_as_missing(self):
        self.assertEqual(self.store_returning(b"{}").read_menus({"stalls": {"kopi": {}}}), {KEY: None})

    def test_the_log_is_not_judged_as_a_menu(self):
        self.assertEqual(self.store_returning(b'{"changes": []}').read_log(), {"changes": []})


class RecentVersions(unittest.TestCase):
    """recover trusts this order and this scope, so both are tested against what S3 returns."""

    def store(self, versions):
        fake = mock.Mock()
        fake.list_object_versions.return_value = {"Versions": versions}
        fake.get_object.side_effect = lambda **_: {"Body": io.BytesIO(b'{"items": []}')}
        with mock.patch.object(s3_store.boto3, "client", return_value=fake):
            return s3_store.MenuStore("bucket", "ap-southeast-2")

    def version(self, version_id, minute, key=KEY):
        when = datetime.datetime(2026, 10, 28, 4, minute, tzinfo=datetime.UTC)
        return {"Key": key, "VersionId": version_id, "Size": 1, "IsLatest": False, "LastModified": when}

    def test_newest_comes_first_whatever_order_s3_lists_them(self):
        store = self.store([self.version("old", 1), self.version("new", 9), self.version("mid", 5)])
        self.assertEqual([v["version_id"] for v in store.recent_versions(KEY, 3)], ["new", "mid", "old"])

    def test_a_similar_key_is_never_part_of_the_history(self):
        store = self.store([self.version("v1", 1), self.version("other", 9, key=f"{KEY}.bak")])
        self.assertEqual([v["version_id"] for v in store.recent_versions(KEY, 5)], ["v1"])


class CurrentVersions(unittest.TestCase):
    def test_reads_every_menu_and_the_log_and_names_a_missing_one(self):
        missing = s3_store.ClientError({"Error": {"Code": "404"}}, "HeadObject")
        fake = mock.Mock()
        fake.head_object.side_effect = [{"VersionId": "v1"}, missing, {"VersionId": "v9"}]
        with mock.patch.object(s3_store.boto3, "client", return_value=fake):
            versions = s3_store.MenuStore("bucket", "ap-southeast-2").current_versions(
                {"stalls": {"kopi": {}, "seduh": {}}}
            )
        self.assertEqual(
            versions,
            {"menus/stall-kopi.json": "v1", "menus/stall-seduh.json": None, s3_store.LOG_KEY: "v9"},
        )

    def test_a_real_error_is_not_read_as_missing(self):
        fake = mock.Mock()
        fake.head_object.side_effect = s3_store.ClientError({"Error": {"Code": "403"}}, "HeadObject")
        with (
            mock.patch.object(s3_store.boto3, "client", return_value=fake),
            self.assertRaises(s3_store.ClientError),
        ):
            s3_store.MenuStore("bucket", "ap-southeast-2").current_versions({"stalls": {"kopi": {}}})


if __name__ == "__main__":
    unittest.main()
