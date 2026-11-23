resource "aws_eip" "spare" {
  domain = "vpc"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
