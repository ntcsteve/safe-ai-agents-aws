# No defaults: these come from ./kopi, the one way to run this.

variable "bucket" {
  description = "The menus bucket."
  type        = string
}

variable "region" {
  description = "The menus bucket's region."
  type        = string
}

variable "organizational_unit_id" {
  description = "Clumio OU, from clumio.env. Blank uses the token's default."
  type        = string
}

variable "protect" {
  description = "false connects only. Clumio must discover a new bucket before it can be protected."
  type        = bool
}
