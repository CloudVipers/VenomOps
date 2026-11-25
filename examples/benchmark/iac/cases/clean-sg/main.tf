resource "aws_security_group" "internal" {
  name        = "bench-internal"
  description = "HTTPS from the corporate network"
  vpc_id      = "vpc-0123456789abcdef0"

  ingress {
    description = "HTTPS from the corporate network"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
