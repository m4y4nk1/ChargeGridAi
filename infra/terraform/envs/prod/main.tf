terraform {
  required_version = ">= 1.6"
  backend "s3" {
    bucket         = "chargegrid-tfstate" # created once by hand: versioned, encrypted
    key            = "prod/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "chargegrid-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = "ap-south-1"
  default_tags { tags = { Project = "chargegrid", Environment = "prod" } }
}

variable "github_repo" {
  description = "owner/repo that deploys through GitHub OIDC"
  type        = string
}

module "platform" {
  source      = "../../modules/platform"
  env         = "prod"
  github_repo = var.github_repo
  db_multi_az = true
  node_min    = 3
  node_max    = 10
}

output "platform" { value = module.platform }
