resource "aws_instance" "dev_box" {
  ami           = "ami-0123456789abcdef0"
  instance_type = "m5.8xlarge"

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
