resource "aws_acm_certificate" "dashboard" {
  provider          = aws.us_east_1
  domain_name       = var.dashboard_domain
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_acm_certificate_validation" "dashboard" {
  provider        = aws.us_east_1
  certificate_arn = aws_acm_certificate.dashboard.arn
}