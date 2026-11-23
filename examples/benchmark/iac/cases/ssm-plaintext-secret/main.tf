resource "aws_ssm_parameter" "db_password" {
  name  = "/bench/db_password"
  type  = "String"
  value = "placeholder-not-a-real-secret"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
