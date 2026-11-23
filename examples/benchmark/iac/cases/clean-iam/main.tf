resource "aws_iam_policy" "reader" {
  name = "bench-reader"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "arn:aws:s3:::bench-clean/*"
    }]
  })
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
