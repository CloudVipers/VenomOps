resource "aws_iam_policy" "deploy" {
  name = "bench-deploy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
    }]
  })
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
