# Fallas deliberadas para arch-committee: RDS de producción en una sola AZ, sin backups, sin cifrado, expuesta a Internet
# y sobredimensionada.
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"

  tags = {
    Name        = "orders"
    Environment = "prod"
  }
}

resource "aws_subnet" "db_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "us-east-1a"
}

resource "aws_subnet" "db_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "us-east-1b"
}

resource "aws_db_subnet_group" "orders" {
  name       = "orders"
  subnet_ids = [aws_subnet.db_a.id, aws_subnet.db_b.id]
}

resource "aws_security_group" "db" {
  name   = "orders-db"
  vpc_id = aws_vpc.main.id

  ingress {
    description = "postgres from anywhere"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "orders" {
  identifier                  = "orders-prod"
  engine                      = "postgres"
  engine_version              = "16.3"
  instance_class              = "db.r6g.8xlarge"
  allocated_storage           = 500
  db_name                     = "orders"
  username                    = "orders_admin"
  manage_master_user_password = true

  multi_az                = false
  publicly_accessible     = true
  storage_encrypted       = false
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false

  db_subnet_group_name   = aws_db_subnet_group.orders.name
  vpc_security_group_ids = [aws_security_group.db.id]
}
