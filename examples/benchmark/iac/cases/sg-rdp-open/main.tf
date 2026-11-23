resource "aws_security_group" "windows" {
  name        = "bench-windows"
  description = "RDP access"
  vpc_id      = "vpc-0123456789abcdef0"

  ingress {
    description = "RDP access"
    from_port   = 3389
    to_port     = 3389
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
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
