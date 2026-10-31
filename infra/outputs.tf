output "region" { value = var.region }
output "bucket" { value = aws_s3_bucket.menus.id }
output "url" { value = "https://${aws_cloudfront_distribution.kiosk.domain_name}/" }
output "investigator_harness_arn" { value = aws_bedrockagentcore_harness.investigator.arn }
output "tools_function" { value = aws_lambda_function.tools.function_name }
output "tools_role_arn" { value = aws_iam_role.tools.arn }
output "investigator_role_arn" { value = aws_iam_role.investigator.arn }
output "gateway_arn" { value = aws_bedrockagentcore_gateway.kiosk.gateway_arn }
