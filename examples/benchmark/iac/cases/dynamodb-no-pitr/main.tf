resource "aws_dynamodb_table" "orders" {
  name         = "bench-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
