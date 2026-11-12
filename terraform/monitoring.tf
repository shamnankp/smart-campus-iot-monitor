locals {
  alarms = {
    processor-errors    = { fn = aws_lambda_function.processor.function_name, metric = "Errors" }
    processor-throttles = { fn = aws_lambda_function.processor.function_name, metric = "Throttles" }
    api-errors          = { fn = aws_lambda_function.api.function_name, metric = "Errors" }
    api-throttles       = { fn = aws_lambda_function.api.function_name, metric = "Throttles" }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda" {
  for_each            = local.alarms
  alarm_name          = "${local.name}-${each.key}"
  namespace           = "AWS/Lambda"
  metric_name         = each.value.metric
  dimensions          = { FunctionName = each.value.fn }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}