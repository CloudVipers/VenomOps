resource "aws_security_group" "web" {
  name        = "bench-web"
  description = "SSH access"
  vpc_id      = "vpc-0123456789abcdef0"

  ingress {
    description = "SSH access"
    from_port   = 22
    to_port     = 22
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
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_instance" "web" {
  ami           = "ami-0123456789abcdef0"
  instance_type = "m5.4xlarge"

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
  }

}
