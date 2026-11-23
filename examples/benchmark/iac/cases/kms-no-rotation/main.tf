resource "aws_kms_key" "data" {
  description         = "bench data key"
  enable_key_rotation = false
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
