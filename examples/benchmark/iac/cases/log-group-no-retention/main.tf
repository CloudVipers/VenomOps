resource "aws_cloudwatch_log_group" "app" {
  name = "/bench/app"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
