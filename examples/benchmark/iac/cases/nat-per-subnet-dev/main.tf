resource "aws_vpc" "main" {
  cidr_block = "10.20.0.0/16"
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.0.0/24"
  availability_zone = "us-east-1a"
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_subnet" "private" {
  count             = 3
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${count.index + 1}.0/24"
  availability_zone = element(["us-east-1a", "us-east-1b", "us-east-1c"], count.index)
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_eip" "nat" {
  count  = 3
  domain = "vpc"
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_nat_gateway" "per_subnet" {
  count         = 3
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public.id
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}

resource "aws_route_table" "private" {
  count  = 3
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.per_subnet[count.index].id
  }
  tags = {
    Environment = "dev"
    Owner       = "team-a"
    Project     = "bench"
  }
}
