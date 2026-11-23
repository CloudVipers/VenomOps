resource "aws_sns_topic" "events" {
  name              = "bench-events"
  kms_master_key_id = "alias/aws/sns"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_sns_topic_policy" "events" {
  arn = aws_sns_topic.events.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "SNS:Publish"
      Resource  = "arn:aws:sns:us-east-1:123456789012:bench-events"
    }]
  })
}
