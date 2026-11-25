resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  size              = 200
  type              = "io1"
  encrypted         = true
  iops              = 10000

  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}
