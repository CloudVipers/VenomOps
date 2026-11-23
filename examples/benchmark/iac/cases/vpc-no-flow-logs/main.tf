resource "aws_vpc" "main" {
  cidr_block = "10.30.0.0/16"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
