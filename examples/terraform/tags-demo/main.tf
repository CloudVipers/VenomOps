# Falla deliberada (TF-TAG-001): faltan los tags obligatorios Environment y Owner.
resource "aws_s3_bucket" "reports" {
  bucket = "example-demo-reports-bucket"

  tags = {
    Name = "reports"
  }
}

resource "aws_ebs_volume" "scratch" {
  availability_zone = "us-east-1a"
  size              = 10
}
