terraform {
  required_version = ">= 1.6"
  backend "s3" {
    bucket         = "chargegrid-tfstate" # created once by hand: versioned, encrypted
    key            = "staging/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "chargegrid-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = "ap-south-1"
  default_tags { tags = { Project = "chargegrid", Environment = "staging" } }
}

variable "github_repo" {
  description = "owner/repo that deploys through GitHub OIDC"
  type        = string
}

module "platform" {
  source      = "../../modules/platform"
  env         = "staging"
  github_repo = var.github_repo
  vpc_cidr    = "10.41.0.0/16"
}

output "platform" { value = module.platform }
