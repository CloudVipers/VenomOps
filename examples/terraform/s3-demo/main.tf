# Falla deliberada (TF-S3-001): el bucket no tiene cifrado en reposo.
resource "aws_s3_bucket" "demo_logs" {
  bucket = "example-demo-logs-bucket"

  tags = {
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "demo_logs" {
  bucket = aws_s3_bucket.demo_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}
