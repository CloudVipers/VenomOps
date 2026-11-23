resource "aws_ssm_parameter" "api_url" {
  name  = "/bench/api_url"
  type  = "String"
  value = "https://api.example.com"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
