resource "aws_instance" "tool" {
  ami           = "ami-0123456789abcdef0"
  instance_type = "t3.micro"

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
  }

  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}
