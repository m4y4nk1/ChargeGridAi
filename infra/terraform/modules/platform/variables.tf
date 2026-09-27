variable "env" {
  description = "staging | prod"
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "eks_version" {
  type    = string
  default = "1.31"
}

variable "node_instance_types" {
  description = "Graviton nodes; images are built multi-arch"
  type        = list(string)
  default     = ["m7g.xlarge"]
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 6
}

variable "db_instance_class" {
  type    = string
  default = "db.r7g.large"
}

variable "db_allocated_gb" {
  type    = number
  default = 200
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.medium"
}

variable "github_repo" {
  description = "owner/repo allowed to deploy through GitHub OIDC"
  type        = string
}
