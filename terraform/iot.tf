resource "aws_iot_thing" "node" {
  for_each   = var.devices
  name       = each.key
   attributes = { zone = replace(each.value, " ", "_") }
}

resource "aws_iot_certificate" "node" {
  for_each = var.devices
  active   = true
}

resource "aws_iot_thing_principal_attachment" "node" {
  for_each  = var.devices
  thing     = aws_iot_thing.node[each.key].name
  principal = aws_iot_certificate.node[each.key].arn
}

# Least privilege: connect only as own thing name, publish/subscribe only to designated topics
resource "aws_iot_policy" "device" {
  name = "${local.name}-device-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "iot:Connect",
        Resource = "arn:aws:iot:${var.region}:${local.account}:client/$${iot:Connection.Thing.ThingName}" },
      { Effect = "Allow", Action = "iot:Publish",
        Resource = "arn:aws:iot:${var.region}:${local.account}:topic/campus/building/sensors" },
      { Effect = "Allow", Action = "iot:Subscribe",
        Resource = "arn:aws:iot:${var.region}:${local.account}:topicfilter/campus/building/commands" },
      { Effect = "Allow", Action = "iot:Receive",
        Resource = "arn:aws:iot:${var.region}:${local.account}:topic/campus/building/commands" }
    ]
  })
}

resource "aws_iot_policy_attachment" "node" {
  for_each = var.devices
  policy   = aws_iot_policy.device.name
  target   = aws_iot_certificate.node[each.key].arn
}

# Write certs/keys locally for the simulator
resource "local_file" "cert" {
  for_each = var.devices
  filename = "${path.module}/../simulator/certs/${each.key}.cert.pem"
  content  = aws_iot_certificate.node[each.key].certificate_pem
}
resource "local_sensitive_file" "key" {
  for_each = var.devices
  filename = "${path.module}/../simulator/certs/${each.key}.private.key"
  content  = aws_iot_certificate.node[each.key].private_key
}

data "aws_iot_endpoint" "ats" { endpoint_type = "iot:Data-ATS" }

# Role letting the Rules Engine write raw data to S3
resource "aws_iam_role" "iot_s3" {
  name = "${local.name}-iot-rule-s3"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "iot.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy" "iot_s3" {
  role = aws_iam_role.iot_s3.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "s3:PutObject", Resource = "${aws_s3_bucket.raw.arn}/*" }]
  })
}

# IoT Core Rules Engine: one rule, two actions (Lambda + raw backup to S3)
resource "aws_iot_topic_rule" "telemetry" {
  name        = "smart_campus_telemetry"
  enabled     = true
  sql         = "SELECT * FROM 'campus/building/sensors'"
  sql_version = "2016-03-23"

  lambda { function_arn = aws_lambda_function.processor.arn }

  s3 {
    bucket_name = aws_s3_bucket.raw.bucket
    key         = "raw/$${topic()}/$${timestamp()}.json"
    role_arn    = aws_iam_role.iot_s3.arn
  }
}