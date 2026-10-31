# No defaults: these come from config.env through ./kopi, the one way to deploy.

variable "region" {
  description = "AWS region."
  type        = string
}

variable "model" {
  description = "Bedrock inference profile ID."
  type        = string
}

variable "scenario" {
  description = "Scenario file name under scenario/, without .json."
  type        = string
}
