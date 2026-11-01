"""Preflight runs before ./kopi up creates anything, so a missing tool, bad credentials or an unusable model
is named in seconds, not partway through a deploy. AWS is faked at its seam."""

import types
import unittest

from botocore.exceptions import ClientError, NoCredentialsError

from kiosk import config, preflight

SETTINGS = config.Config(region="ap-southeast-2", model="au.anthropic.claude-sonnet-4-6", scenario="kopi")
ACCOUNT = "111122223333"


def denied(code, message):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Operation")


def fake_client(identity=None, model_error=None, asked=None):
    """A boto3.client stand-in: sts answers with the identity, bedrock-runtime with the model's reply."""

    def get_caller_identity():
        if isinstance(identity, Exception):
            raise identity
        return {"Account": ACCOUNT}

    def converse(**request):
        asked.append(request) if asked is not None else None
        if model_error:
            raise model_error
        return {}

    def client(service, region_name=None):
        if service == "sts":
            return types.SimpleNamespace(get_caller_identity=get_caller_identity)
        assert region_name == SETTINGS.region  # the model is tested where it will run
        return types.SimpleNamespace(converse=converse)

    return client


def run(which=lambda name: f"/bin/{name}", **fake):
    return preflight.check(SETTINGS, which=which, client=fake_client(**fake))


class Passes(unittest.TestCase):
    def test_returns_the_target_with_the_account_masked(self):
        target = run()
        self.assertEqual(target, "account ********3333, region ap-southeast-2")
        self.assertNotIn(ACCOUNT, target)

    def test_the_model_call_is_as_small_as_possible(self):
        asked = []
        run(asked=asked)
        self.assertEqual(asked[0]["modelId"], SETTINGS.model)
        self.assertEqual(asked[0]["inferenceConfig"], {"maxTokens": 1})


class Refused(unittest.TestCase):
    def assert_refused(self, *named, **kwargs):
        with self.assertRaises(preflight.PreflightFailed) as caught:
            run(**kwargs)
        for word in named:
            self.assertIn(word, str(caught.exception))
        self.assertNotIn(ACCOUNT, str(caught.exception))

    def test_terraform_missing(self):
        self.assert_refused("Terraform", "install", which=lambda name: None)

    def test_no_credentials(self):
        self.assert_refused("credentials", identity=NoCredentialsError())

    def test_expired_credentials(self):
        self.assert_refused("credentials", identity=denied("ExpiredToken", "token expired"))

    def test_model_id_wrong(self):
        self.assert_refused("MODEL", "config.env", model_error=denied("ValidationException", "invalid model"))

    def test_the_anthropic_form_not_filled_points_at_the_form(self):
        """Bedrock answers 404 until an account submits Anthropic's use case details. MODEL alone would mislead."""
        self.assert_refused("Anthropic use case form", model_error=denied("ResourceNotFoundException", "not submitted"))

    def test_model_access_not_granted(self):
        self.assert_refused("Bedrock", "access", model_error=denied("AccessDeniedException", "no access"))

    def test_an_account_number_in_aws_text_is_masked(self):
        error = denied("AccessDeniedException", f"arn:aws:iam::{ACCOUNT}:user/me is not authorized")
        self.assert_refused("Bedrock", model_error=error)


if __name__ == "__main__":
    unittest.main()
