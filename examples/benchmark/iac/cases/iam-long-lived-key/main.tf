resource "aws_iam_user" "ci" {
  name = "bench-ci"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_iam_access_key" "ci" {
  user = aws_iam_user.ci.name
}
