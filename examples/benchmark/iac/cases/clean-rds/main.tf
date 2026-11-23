resource "aws_db_instance" "orders" {
  identifier = "bench-orders"
  engine = "postgres"
  engine_version = "16"
  instance_class = "db.t3.medium"
  allocated_storage = 50
  username = "app"
  manage_master_user_password = true
  storage_encrypted = true
  multi_az = true
  publicly_accessible = false
  backup_retention_period = 14
  deletion_protection = true
  skip_final_snapshot = false
  final_snapshot_identifier = "bench-orders-final"
  tags = {
    Environment = "prod"
    Owner       = "team-a"
    Project     = "bench"
  }
}
