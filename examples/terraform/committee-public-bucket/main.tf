# Fallas deliberadas para arch-committee (seguridad): bucket público sin cifrado ni versionado y una política IAM con permisos totales.
resource "aws_s3_bucket" "assets" {
  bucket = "example-committee-assets"

  tags = {
    Environment = "prod"
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket = aws_s3_bucket.assets.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "assets_public_read" {
  bucket = aws_s3_bucket.assets.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicRead"
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject"]
      # ARN literal: si se usara aws_s3_bucket.assets.arn, Terraform no conoce el valor hasta aplicar y el plan
      # no mostraría el contenido de la política (un plan solo permite revisar lo que ya es conocido).
      Resource  = "arn:aws:s3:::example-committee-assets/*"
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.assets]
}

resource "aws_iam_policy" "deploy" {
  name = "committee-deploy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
    }]
  })
}
