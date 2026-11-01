# Clumio protects the menus bucket, so a person-approved Backtrack can roll one object back.
# Reuses the modules of get-started-clumio-aws, pinned to a release.

data "aws_caller_identity" "me" {}

locals {
  account_id = data.aws_caller_identity.me.account_id
  # <app>-<data>-<operations>-<retention>
  protection_name = "kopi-menus-backtrack-1d-svstd-30d"
}

resource "clumio_aws_connection" "this" {
  account_native_id = local.account_id
  aws_region        = var.region
  description       = "safe-ai-agents-aws: Backtrack on the menus bucket"
}

module "clumio_iam" {
  source     = "git::https://github.com/ntcsteve/get-started-clumio-aws.git//modules/clumio-account-iam?ref=v1.1.2"
  connection = clumio_aws_connection.this
  iam_prefix = "kopi-" # So these roles stay separate from any Clumio roles already in the account.
  workloads  = ["s3"]
}

module "menus_protection" {
  count  = var.protect ? 1 : 0
  source = "git::https://github.com/ntcsteve/get-started-clumio-aws.git//modules/clumio-s3-protection?ref=v1.1.2"

  account_id   = local.account_id
  region       = var.region
  name         = local.protection_name
  protect_by   = "name"
  bucket_names = [var.bucket]

  backtrack_retention_months = 1  # This demo rolls back hours, not months.
  standard_retention_days    = 30 # Clumio's S3 minimum: the copy outside the account.

  depends_on = [module.clumio_iam]
}
