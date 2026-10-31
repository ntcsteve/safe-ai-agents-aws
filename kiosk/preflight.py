"""Before ./kopi up creates anything: is Terraform here, do the AWS credentials work, can this account use
the model? Each failure names the fix in seconds, instead of partway through a deploy."""

import re
import shutil

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ACCOUNT_ID = re.compile(r"\b\d{12}\b")


class PreflightFailed(RuntimeError):
    """Something ./kopi up needs is missing. Nothing was created."""


def check(settings, which=shutil.which, client=boto3.client):
    """The deploy target in words, with the account masked, or PreflightFailed naming the fix."""
    if not which("terraform"):
        raise PreflightFailed("Terraform not found. Next: install Terraform 1.10 or newer")
    account = _account_id(client)
    _require_model(settings, client)
    return f"account {'*' * 8}{account[-4:]}, region {settings.region}"


def _account_id(client):
    try:
        return client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as error:
        raise PreflightFailed(f"no working AWS credentials ({_masked(error)}). Next: sign in to AWS") from None


def _require_model(settings, client):
    """One real one-token call: the only test that proves both the ID and this account's access."""
    try:
        client("bedrock-runtime", region_name=settings.region).converse(
            modelId=settings.model,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 1},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "AccessDeniedException":
            raise PreflightFailed(
                f"no Bedrock access to {settings.model} ({_masked(error)}). "
                "Next: request model access in the Bedrock console"
            ) from None
        raise PreflightFailed(
            f"{settings.model} cannot be used in {settings.region} ({_masked(error)}). Next: fix MODEL in config.env"
        ) from None
    except BotoCoreError as error:
        raise PreflightFailed(f"could not reach Bedrock ({_masked(error)}). Next: check your network") from None


def _masked(error):
    return ACCOUNT_ID.sub("<account>", str(error))
