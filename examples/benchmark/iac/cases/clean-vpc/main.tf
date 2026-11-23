resource "aws_vpc" "main" {
  cidr_block = "10.40.0.0/16"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_flow_log" "main" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::bench-flow-logs"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
