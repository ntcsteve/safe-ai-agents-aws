terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "= 6.70.0" }
    archive = { source = "hashicorp/archive", version = "= 2.8.1" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "safe-ai-agents-aws" }
  }
}
