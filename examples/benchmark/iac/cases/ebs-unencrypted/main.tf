resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  size              = 50
  type              = "gp3"
  encrypted         = false

  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
