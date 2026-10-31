"""Menus and the maintenance log in S3. The only module that reads or writes the bucket."""

import json

import boto3
from botocore.exceptions import ClientError

from kiosk import menu

LOG_KEY = "changes/maintenance-log.json"
# Short browser and edge cache, so phones see a rollback within seconds (see infra cache policy).
MENU_CACHE_CONTROL = "max-age=2"


class MenuStore:
    def __init__(self, bucket, region):
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)

    def read_menus(self, scenario):
        """key -> menu for every stall in the scenario; None where the object is missing."""
        return {menu.menu_key(stall_id): self._read_json(menu.menu_key(stall_id)) for stall_id in scenario["stalls"]}

    def write_menu(self, key, stall_menu):
        """Write one menu object. Returns the new S3 version ID, the receipt for this write."""
        response = self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=menu.encode_menu(stall_menu),
            ContentType="application/json; charset=utf-8",
            CacheControl=MENU_CACHE_CONTROL,
        )
        return response["VersionId"]

    def recent_versions(self, key, count):
        """The key's newest versions (delete markers included), each with its menu, or None if deleted."""
        listing = self.s3.list_object_versions(Bucket=self.bucket, Prefix=key, MaxKeys=50)
        entries = [{**v, "deleted": False} for v in listing.get("Versions", []) if v["Key"] == key]
        entries += [{**m, "deleted": True, "Size": 0} for m in listing.get("DeleteMarkers", []) if m["Key"] == key]
        entries.sort(key=lambda v: (v["LastModified"], v.get("IsLatest", False)), reverse=True)
        return [
            {
                "version_id": v["VersionId"],
                "size": v["Size"],
                "last_modified": v["LastModified"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "menu": None
                if v["deleted"]
                else _parsed(self.s3.get_object(Bucket=self.bucket, Key=key, VersionId=v["VersionId"])["Body"].read()),
            }
            for v in entries[:count]
        ]

    def read_log(self):
        return self._read_json(LOG_KEY) or {"changes": []}

    def write_log(self, log):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=LOG_KEY,
            Body=(json.dumps(log, indent=2) + "\n").encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )

    def _read_json(self, key):
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as error:
            if error.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise
        return _parsed(body)


def _parsed(body):
    """The object's JSON, or None if it is not valid: unreadable counts as missing, so it reads as BROKEN
    and ./kopi reset can still run. Raising here would block the command that fixes it."""
    try:
        return json.loads(body)
    except ValueError:
        return None
