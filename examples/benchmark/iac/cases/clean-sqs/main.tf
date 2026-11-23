resource "aws_sqs_queue" "jobs" {
  name                    = "bench-jobs"
  sqs_managed_sse_enabled = true
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
