resource "aws_launch_template" "web" {
  name_prefix   = "bench-web-"
  image_id      = "ami-0123456789abcdef0"
  instance_type = "t3.small"

  metadata_options {
    http_tokens = "required"
  }
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_autoscaling_group" "web" {
  name               = "bench-web"
  min_size           = 2
  max_size           = 4
  availability_zones = ["us-east-1a"]

  launch_template {
    id      = aws_launch_template.web.id
    version = "$Latest"
  }

  tag {
    key                 = "Environment"
    value               = "prod"
    propagate_at_launch = true
  }
}
