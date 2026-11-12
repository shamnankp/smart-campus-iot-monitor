resource "aws_dynamodb_table" "telemetry" {
  name           = "${local.name}-telemetry"
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
  hash_key       = "DeviceID"
  range_key      = "Timestamp"
  attribute {
    name = "DeviceID"
    type = "S"
  }
  attribute {
    name = "Timestamp"
    type = "N"
  }
  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "alerts" {
  name           = "${local.name}-alerts"
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
  hash_key       = "DeviceID"
  range_key      = "Timestamp"
  attribute {
    name = "DeviceID"
    type = "S"
  }
  attribute {
    name = "Timestamp"
    type = "N"
  }
  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }
}

resource "aws_s3_bucket" "raw" {
  bucket        = "${local.name}-raw-${local.account}"
  force_destroy = true
}
resource "aws_s3_bucket" "web" {
  bucket        = "${local.name}-web-${local.account}"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "block" {
  for_each                = { raw = aws_s3_bucket.raw.id, web = aws_s3_bucket.web.id }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_sns_topic" "alerts" { name = "${local.name}-alerts" }
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}