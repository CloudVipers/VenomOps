resource "aws_sqs_queue" "orders" {
  name                    = "bench-orders"
  sqs_managed_sse_enabled = false
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
