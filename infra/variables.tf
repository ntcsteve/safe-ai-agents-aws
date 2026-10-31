variable "region" {
  description = "Sydney only: Claude runs on the Australian geo profile there."
  type        = string
  default     = "ap-southeast-2"
  validation {
    condition     = var.region == "ap-southeast-2"
    error_message = "This runs in ap-southeast-2 (Sydney) only."
  }
}

variable "scenario" {
  description = "Scenario file name under scenario/, without .json."
  type        = string
  default     = "kopi"
}
