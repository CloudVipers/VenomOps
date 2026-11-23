resource "aws_iam_policy" "ci" {
  name = "bench-ci"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["iam:PassRole", "ec2:RunInstances"]
      Resource = "*"
    }]
  })
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
