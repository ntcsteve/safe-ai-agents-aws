"""A menu object that is not valid JSON counts as missing: BROKEN, so ./kopi reset can still run. If it
crashed instead, the state check would fail first and block the command that fixes it."""

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


if __name__ == "__main__":
    unittest.main()
