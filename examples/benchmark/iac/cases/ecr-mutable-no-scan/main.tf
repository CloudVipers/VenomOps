resource "aws_ecr_repository" "app" {
  name                 = "bench-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
