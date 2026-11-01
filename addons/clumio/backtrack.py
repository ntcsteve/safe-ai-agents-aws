"""The Clumio add-on: connect a bucket, roll one object back to the last sync, and disconnect.

Everything Clumio lives in this folder, so deleting it leaves the AWS demo working. Settings come from
clumio.env, and only from there. API paths come from clumio-code/clumio-go-sdk. Stdlib only.
"""

import dataclasses
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from kiosk import commands, menu, recovery
from kiosk.s3_store import LOG_KEY

HERE = pathlib.Path(__file__).resolve().parent
ENV_FILE = HERE / "clumio.env"
STATE_FILE = HERE / "terraform.tfstate"
GUIDE = "addons/clumio/README.md"
RESTORE_ACCEPT = "application/api.clumio.restored-aws-s3-buckets=v1+json"
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 600  # a rollback of one small object took 67 s; ten minutes means something is wrong
SEE_BUCKET_POLL_SECONDS = 30
SEE_BUCKET_MAX_SECONDS = 1800
VERSION_LOOKBACK = 50  # how far back recover looks to judge the version Clumio previews


class ClumioRefused(recovery.RecoveryRefused):
    """Clumio could not do exactly what was asked. The message names the state and the next step."""


@dataclasses.dataclass(frozen=True)
class Settings:
    token: str = dataclasses.field(repr=False)
    base_url: str
    ou_id: str


def parse_settings(text):
    """clumio.env's three values. Parsed, never sourced: a sourced file could set AWS_PROFILE and silently move
    a run to another account, so any other key is refused."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key not in ("CLUMIO_API_TOKEN", "CLUMIO_API_BASE_URL", "CLUMIO_OU_ID"):
            raise ClumioRefused(f"CLUMIO NOT SET UP: unknown key '{key}' in clumio.env. Next: see {GUIDE}")
        values[key] = value.strip()
    for key in ("CLUMIO_API_TOKEN", "CLUMIO_API_BASE_URL"):
        if not values.get(key):
            raise ClumioRefused(f"CLUMIO NOT SET UP: {key} is empty in clumio.env. Next: see {GUIDE}")
    if not re.fullmatch(r"https://[^\s/]+/?", values["CLUMIO_API_BASE_URL"]):
        raise ClumioRefused(f"CLUMIO NOT SET UP: CLUMIO_API_BASE_URL must be an https URL. Next: see {GUIDE}")
    return Settings(
        values["CLUMIO_API_TOKEN"], values["CLUMIO_API_BASE_URL"].rstrip("/"), values.get("CLUMIO_OU_ID", "")
    )


def read_settings():
    if not ENV_FILE.exists():
        raise ClumioRefused(f"CLUMIO NOT SET UP: addons/clumio/clumio.env is missing. Next: see {GUIDE}")
    return parse_settings(ENV_FILE.read_text())


def is_active():
    """True when this add-on's Terraform holds resources: Clumio is connected, or partly connected."""
    return bool(_state_resources())


def is_protected():
    """True once the protection exists. Connected alone is not enough: no sync will ever run."""
    return any(r.get("module", "").startswith("module.menus_protection") for r in _state_resources())


def _state_resources():
    return json.loads(STATE_FILE.read_text()).get("resources", []) if STATE_FILE.exists() else []


class ClumioBacktrack:
    def __init__(self, settings):
        self.base = settings.base_url
        self.token = settings.token

    def find_bucket(self, name):
        """(bucket_id, environment_id, last sync or None) of a bucket Clumio has discovered."""
        flt = urllib.parse.quote(json.dumps({"name": {"$in": [name]}}))
        items = self._call(
            "GET", f"/datasources/aws/s3-buckets?filter={flt}", "application/api.clumio.aws-s3-buckets=v1+json"
        )
        found = [b for b in items.get("_embedded", {}).get("items", []) if b.get("name") == name]
        if len(found) != 1:
            raise ClumioRefused(f"Clumio sees {len(found)} buckets named {name}; expected 1")
        return found[0]["id"], found[0]["environment_id"], found[0].get("last_backtrack_sync_timestamp")

    def preview(self, bucket_id, body):
        """The objects a rollback with this scope would restore."""
        started = self._call("POST", f"/restores/aws/s3-buckets/{bucket_id}/previews", RESTORE_ACCEPT, body)
        self.wait(started["task_id"])
        details = self._call(
            "GET", f"/restores/aws/s3-buckets/{bucket_id}/previews/{started['preview_id']}", RESTORE_ACCEPT
        )
        return details.get("objects", [])

    def rollback(self, body):
        return self._call("POST", "/restores/aws/s3-buckets", RESTORE_ACCEPT, body)["task_id"]

    def wait(self, task_id, on_wait=None):
        """Poll a task until it ends; returns the task. Raises if it fails or exceeds MAX_WAIT_SECONDS."""
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            task = self._call("GET", f"/tasks/{task_id}", "application/api.clumio.tasks=v1+json")
            if task.get("status") == "completed":
                return task
            if task.get("status") in ("failed", "aborted", "canceled"):
                raise ClumioRefused(
                    f"CLUMIO TASK FAILED: {task_id} {task['status']}. Next: open it in the Clumio console"
                )
            if on_wait:
                on_wait(task.get("status"))
            time.sleep(POLL_SECONDS)
        raise ClumioRefused(
            f"CLUMIO TASK FAILED: {task_id} still running after {MAX_WAIT_SECONDS} s. "
            "Next: check the Clumio console, and do not submit it again"
        )

    def _call(self, method, path, accept, body=None):
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": accept, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ClumioRefused(
                f"CLUMIO API FAILED: {method} {path.split('?')[0]} HTTP {error.code} {error.read()[:300]!r}. "
                f"Next: check your token, see {GUIDE}"
            ) from None
        except urllib.error.URLError as error:
            raise ClumioRefused(f"CLUMIO API FAILED: {error.reason}. Next: check CLUMIO_API_BASE_URL") from None


def _scope(key):
    # Exact match, not a prefix: "menus/stall-kopi.json" as a prefix also matches "...json.bak".
    # Latest version as of the point in time, as the console does. False lists every older version
    # too (proven live: 6 versions of one key), which the scope gate rightly refuses.
    return {"object_key_matches": key, "is_latest_version_only": True}


def preview_request(key, point):
    return {
        "type": "rollback",
        "point_in_time_recovery": {"restore_end_timestamp": point},
        "object_filters": _scope(key),
    }


def rollback_request(bucket_id, environment_id, bucket_name, key, point):
    return {
        "type": "rollback",
        "source": {
            "bucket_id": bucket_id,
            "point_in_time_recovery": {"restore_end_timestamp": point},
            "object_filters": _scope(key),
        },
        "target": {"bucket_name": bucket_name, "environment_id": environment_id},  # in place: same bucket
    }


def require_single_key(objects, key):
    """The one previewed object, or ClumioRefused naming what the preview actually holds."""
    keys = [o.get("object_key") for o in objects]
    if keys != [key]:
        raise ClumioRefused(f"WRONG SCOPE: the preview lists {keys}, expected only {key}. Nothing rolled back.")
    return objects[0]


def connect(demo):
    """Connect the account and protect the bucket. Clumio must discover a new bucket before it can protect it,
    so a first connect takes two applies with a wait between them. Safe to rerun: protect=false on a bucket
    that is already protected would unprotect it, so a bucket Clumio can see goes straight to protect=true."""
    settings = read_settings()
    client = ClumioBacktrack(settings)
    bucket, region = demo.outputs["bucket"], demo.outputs["region"]
    variables = _variables(settings, bucket, region)
    _terraform(settings, "init", "-input=false", "-lockfile=readonly")
    if not _sees(client, bucket):
        _terraform(settings, "apply", *variables, "-var=protect=false")
        _wait_until_seen(client, bucket)
    _terraform(settings, "apply", *variables, "-var=protect=true")
    commands.teach(
        "Clumio is connected and protects the menus bucket.",
        "it can only roll back to a daily sync, so the first one can take up to a day.",
        "./kopi shows when that first sync has run. Leave the menus HEALTHY until then",
    )


def recover(demo):
    """Roll the one bad object back to Clumio's last sync, after a person types ROLL BACK."""
    if not is_protected():
        raise ClumioRefused("CLUMIO NOT SET UP: this bucket is not protected. Next: ./kopi reset, then ./kopi clumio")
    client = ClumioBacktrack(read_settings())
    bucket = demo.outputs["bucket"]
    stall = demo.scenario["fault"]["stall"]
    key = menu.menu_key(stall)
    bucket_id, environment_id, point = client.find_bucket(bucket)
    if not point:
        raise ClumioRefused("CLUMIO NO SYNC: nothing to roll back to yet. Next: ./kopi recover uses S3 versions")
    menus = demo.store.read_menus(demo.scenario)
    versions = demo.store.recent_versions(key, VERSION_LOOKBACK)
    recovery.choose_restore(menus, versions, stall, demo.scenario)  # refuses unless this one menu is what is wrong
    previewed = require_single_key(client.preview(bucket_id, preview_request(key, point)), key)
    target = _require_good_version(previewed, versions, stall, demo.scenario, point)
    print(f"Preview: {key} goes back to version {target['version_id']}, as of Clumio's last sync at {point}.")
    currency = demo.scenario["currency"]
    for name, price_now, restored in recovery.price_changes(menus[key], target["menu"]):
        shown = "missing" if price_now is None else menu.format_price(price_now, currency)
        print(f"  {name}: {shown} -> {menu.format_price(restored, currency)}")
    print("Every other menu stays as it is.")
    if commands.typed_answer("Approve the rollback? Type ROLL BACK: ") != "ROLL BACK":
        print("PREVIEW ONLY: nothing submitted. Next: ./kopi recover --clumio")
        return 1
    before = demo.store.current_versions(demo.scenario)
    task_id = client.rollback(rollback_request(bucket_id, environment_id, bucket, key, point))
    print(f"Submitted Clumio task {task_id}. It usually takes about 2 minutes.", flush=True)
    client.wait(task_id, on_wait=lambda status: print(f"  {status}...", flush=True))
    after = demo.store.current_versions(demo.scenario)
    others = [changed for changed in recovery.changed_keys(before, after) if changed not in (key, LOG_KEY)]
    if others:
        print(f"WRONG SCOPE AFTER RESTORE: {', '.join(others)} also changed. Next: ./kopi reset, then find out why")
        return 1
    demo.log_restore(key, after[key], f"Clumio task {task_id}")
    code = demo.check()
    if code == 0:
        print(f"RECOVERED: {key} rolled back by Clumio.")
        commands.teach(
            f"you approved it, and S3 shows only {key} changed.",
            f"{commands.promo_line(demo.scenario)}, and Clumio's task is the record of the recovery.",
            "./kopi reset to run it again, or ./kopi down to finish, because this stack costs money",
        )
    return code


def status(bucket):
    """(line for bare ./kopi, next step or None for the usual one), or None when the add-on is not in use.
    Never raises: a Clumio outage must not stop someone finding out where they are."""
    if not is_active():
        return None
    if not is_protected():
        return "Clumio: connected but not protected, so no sync will run", "./kopi clumio"
    try:
        _, _, last_sync = ClumioBacktrack(read_settings()).find_bucket(bucket)
    except ClumioRefused as error:
        return f"Clumio: cannot reach it ({error})", None
    if last_sync:
        return (
            f"Clumio: ready, last sync {last_sync}",
            "./kopi break, then ./kopi observe, then ./kopi recover --clumio",
        )
    return "Clumio: waiting for its first sync. Leave the menus HEALTHY", "./kopi again later. It can take up to a day"


def disconnect():
    """Unprotect the bucket, then remove the connection and Clumio's IAM, from what this add-on recorded."""
    settings = read_settings()
    recorded = _addon_outputs()
    try:
        _terraform(settings, "destroy", *_variables(settings, recorded["bucket"], recorded["region"]))
    except subprocess.CalledProcessError:
        raise ClumioRefused(
            "CLUMIO DISCONNECT FAILED: read the output above. Some tenants refuse to delete a connection. "
            "Next: ask your Clumio admin, then ./kopi down"
        ) from None


def _require_good_version(previewed, versions, stall, scenario, point):
    """The S3 version Clumio would restore, checked against the expected menu. If a bad change sat through a
    sync, that sync captured it: rolling back would restore the fault."""
    target = next((v for v in versions if v["version_id"] == previewed.get("version_id")), None)
    if target is None or not recovery.is_good(target, stall, scenario):
        raise ClumioRefused(
            f"CLUMIO POINT BAD: the sync at {point} does not hold a good menu. "
            "Nothing rolled back. Next: ./kopi recover uses S3 versions"
        )
    return target


def _sees(client, bucket):
    try:
        client.find_bucket(bucket)
    except ClumioRefused:
        return False
    return True


def _wait_until_seen(client, bucket):
    deadline = time.monotonic() + SEE_BUCKET_MAX_SECONDS
    print(f"Waiting for Clumio to discover {bucket}...", flush=True)
    while time.monotonic() < deadline:
        if _sees(client, bucket):
            return
        time.sleep(SEE_BUCKET_POLL_SECONDS)
    raise ClumioRefused(
        f"CLUMIO BUCKET NOT SEEN: {bucket} after {SEE_BUCKET_MAX_SECONDS // 60} minutes. "
        "Nothing is broken. Next: ./kopi clumio again later"
    )


def _variables(settings, bucket, region):
    return [f"-var=bucket={bucket}", f"-var=region={region}", f"-var=organizational_unit_id={settings.ou_id}"]


def _terraform(settings, *args):
    env = {**os.environ, "CLUMIO_API_TOKEN": settings.token, "CLUMIO_API_BASE_URL": settings.base_url}
    subprocess.run(["terraform", f"-chdir={HERE}", *args], check=True, env=env)


def _addon_outputs():
    result = subprocess.run(["terraform", f"-chdir={HERE}", "output", "-json"], capture_output=True, text=True)
    return {name: value["value"] for name, value in json.loads(result.stdout or "{}").items()}
