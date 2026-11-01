terraform {
  required_version = ">= 1.10"
  required_providers {
    clumio = { source = "clumio-code/clumio", version = ">= 0.24.0, < 0.25.0" }
    aws    = { source = "hashicorp/aws", version = "= 6.70.0" }
  }
}

# Credentials come from clumio.env, through ./kopi.
provider "clumio" {
  clumio_organizational_unit_context = var.organizational_unit_id != "" ? var.organizational_unit_id : null
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "safe-ai-agents-aws" }
  }
}
