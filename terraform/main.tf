terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive" }
    local   = { source = "hashicorp/local" }
  }
}

variable "region" { default = "ap-south-1" }
variable "alert_email" {}
variable "auto_actions_enabled" { default = "true" }
variable "dashboard_domain" {}
variable "devices" {
  default = {
    "node-library" = "Library"
    "node-lab"     = "Computer Lab"
    "node-hostel"  = "Hostel Block"
    "node-admin"   = "Admin Block"
    "node-canteen" = "Canteen"
  }
}

provider "aws" { region = var.region }

# CloudFront only accepts ACM certificates created in us-east-1
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

data "aws_caller_identity" "me" {}

locals {
  name    = "smart-campus"
  account = data.aws_caller_identity.me.account_id
}