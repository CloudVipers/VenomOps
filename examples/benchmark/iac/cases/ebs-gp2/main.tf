resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  size              = 50
  type              = "gp2"
  encrypted         = true

  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
