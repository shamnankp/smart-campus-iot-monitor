data "archive_file" "processor" {
  type        = "zip"
  source_file = "${path.module}/../lambda/processor.py"
  output_path = "${path.module}/build/processor.zip"
}
data "archive_file" "api" {
  type        = "zip"
  source_file = "${path.module}/../lambda/api.py"
  output_path = "${path.module}/build/api.zip"
}

locals {
  assume_lambda = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${local.name}-processor"
  retention_in_days = 14
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = 14
}

# Processor role: write telemetry/alerts, publish to SNS, publish commands to one IoT topic only
resource "aws_iam_role" "processor" {
  name               = "${local.name}-processor-role"
  assume_role_policy = local.assume_lambda
}
resource "aws_iam_role_policy" "processor" {
  role = aws_iam_role.processor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:Query"],
        Resource = [aws_dynamodb_table.telemetry.arn, aws_dynamodb_table.alerts.arn] },
      { Effect = "Allow", Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn },
      { Effect = "Allow", Action = "iot:Publish",
        Resource = "arn:aws:iot:${var.region}:${local.account}:topic/campus/building/commands" },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = "${aws_cloudwatch_log_group.processor.arn}:*" }
    ]
  })
}

# API role: read-only
resource "aws_iam_role" "api" {
  name               = "${local.name}-api-role"
  assume_role_policy = local.assume_lambda
}
resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "dynamodb:Query",
        Resource = [aws_dynamodb_table.telemetry.arn, aws_dynamodb_table.alerts.arn] },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = "${aws_cloudwatch_log_group.api.arn}:*" }
    ]
  })
}

resource "aws_lambda_function" "processor" {
  function_name    = "${local.name}-processor"
  role             = aws_iam_role.processor.arn
  runtime          = "python3.12"
  handler          = "processor.handler"
  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256
  timeout          = 10
  environment {
    variables = {
      TELEMETRY_TABLE = aws_dynamodb_table.telemetry.name
      ALERTS_TABLE    = aws_dynamodb_table.alerts.name
      TOPIC_ARN       = aws_sns_topic.alerts.arn
      IOT_ENDPOINT    = data.aws_iot_endpoint.ats.endpoint_address
      AUTO_ACTIONS    = var.auto_actions_enabled
    }
  }
  depends_on = [aws_cloudwatch_log_group.processor]
}

resource "aws_lambda_function" "api" {
  function_name    = "${local.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  handler          = "api.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  timeout          = 10
  environment {
    variables = {
      TELEMETRY_TABLE = aws_dynamodb_table.telemetry.name
      ALERTS_TABLE    = aws_dynamodb_table.alerts.name
      DEVICES         = join(",", keys(var.devices))
    }
  }
  depends_on = [aws_cloudwatch_log_group.api]
}

resource "aws_lambda_permission" "iot" {
  statement_id  = "AllowIoTRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "iot.amazonaws.com"
  source_arn    = aws_iot_topic_rule.telemetry.arn
}