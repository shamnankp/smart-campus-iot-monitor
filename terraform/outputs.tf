output "iot_endpoint"   { value = data.aws_iot_endpoint.ats.endpoint_address }
output "dashboard_url"  { value = "https://${var.dashboard_domain}" }
output "api_url"        { value = aws_apigatewayv2_api.api.api_endpoint }
output "cloudfront_domain" { value = aws_cloudfront_distribution.web.domain_name }