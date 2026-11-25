resource "aws_cloudwatch_log_group" "app" {
  name              = "/bench/app"
  retention_in_days = 30
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
