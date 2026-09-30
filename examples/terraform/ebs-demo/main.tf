# Falla deliberada (TF-EBS-001): volúmenes gp2 que deberían ser gp3.
resource "aws_ebs_volume" "data" {
  availability_zone = "us-east-1a"
  size              = 100
  type              = "gp2"

  tags = {
    Name = "data"
  }
}

resource "aws_ebs_volume" "archive" {
  availability_zone = "us-east-1a"
  size              = 2000
  type              = "gp2"
}
