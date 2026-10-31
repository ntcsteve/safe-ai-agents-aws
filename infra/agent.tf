# The agent: two read-only tools behind an AgentCore Gateway, and the harness that
# reasons over them. Every hop holds only its own permission, so the agent cannot write: nothing in
# its path can. The harness config is not the boundary (a caller can widen allowedTools per invoke);
# the harness role is, and it holds model, gateway and logs only.

# The model's regions and its ARNs come from AWS, so a wrong MODEL fails at plan, before anything exists.
data "aws_bedrock_inference_profile" "model" {
  inference_profile_id = var.model
}

locals {
  stall_list = join(" or ", keys(local.scenario.stalls))
  account_id = data.aws_caller_identity.me.account_id
  tool_files = ["kiosk/__init__.py", "kiosk/menu.py", "kiosk/investigation.py", "kiosk/tool_lambda.py"]
}

# --- Tool Lambda (read-only) ------------------------------------------------------------------

data "archive_file" "tools" {
  type        = "zip"
  output_path = "${path.module}/.build/tools.zip"
  dynamic "source" {
    for_each = local.tool_files
    content {
      content  = file("${path.module}/../${source.value}")
      filename = source.value
    }
  }
  source {
    content  = file("${path.module}/../scenario/${var.scenario}.json")
    filename = "scenario.json" # the same expected menu ./kopi check uses
  }
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tools" {
  name               = "kopi-tools"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# Read menus, their versions, and the maintenance log. No Put, Delete, versioning or lifecycle:
# those are what `./kopi deny` proves are denied. ListBucket is here only so a missing key
# answers 404 (reported as MISSING) instead of 403; it reveals key names, never contents.
resource "aws_iam_role_policy" "tools_read" {
  role = aws_iam_role.tools.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadMenusAndLog"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = ["${aws_s3_bucket.menus.arn}/menus/*", "${aws_s3_bucket.menus.arn}/changes/*"]
      },
      {
        Sid      = "MissingKeysAnswer404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.menus.arn
      },
      {
        Sid       = "ListMenuVersions"
        Effect    = "Allow"
        Action    = "s3:ListBucketVersions"
        Resource  = aws_s3_bucket.menus.arn
        Condition = { StringLike = { "s3:prefix" = ["menus/*"] } }
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.tools.arn}:*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "tools" {
  name              = "/aws/lambda/kopi-tools"
  retention_in_days = 14 # receipts; bounded cost
}

resource "aws_lambda_function" "tools" {
  function_name    = "kopi-tools"
  role             = aws_iam_role.tools.arn
  runtime          = "python3.13"
  handler          = "kiosk.tool_lambda.handler"
  filename         = data.archive_file.tools.output_path
  source_code_hash = data.archive_file.tools.output_base64sha256
  timeout          = 15
  memory_size      = 256
  # No reserved concurrency: this account's Sydney limit is 10, and AWS keeps all 10 unreserved.
  # The bound is upstream instead: the harness allows at most 10 iterations per ask.
  environment {
    variables = { MENU_BUCKET = aws_s3_bucket.menus.id }
  }
  depends_on = [aws_cloudwatch_log_group.tools]
}

# --- Gateway ----------------------------------------------------------------------------------

data "aws_iam_policy_document" "agentcore_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "kopi-gateway"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

resource "aws_iam_role_policy" "gateway" {
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.tools.arn }]
  })
}

resource "aws_bedrockagentcore_gateway" "kiosk" {
  name            = "kopi-gateway"
  role_arn        = aws_iam_role.gateway.arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"
}

resource "aws_bedrockagentcore_gateway_target" "kiosk" {
  name               = "kiosk" # tools reach the agent as kiosk___<tool>
  gateway_identifier = aws_bedrockagentcore_gateway.kiosk.gateway_id
  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.tools.arn
        tool_schema {
          inline_payload {
            name        = "inspect_application"
            description = "Read one stall's current menu and compare every price with the expected menu. Returns per-item status (OK, WRONG, MISSING, UNEXPECTED), the S3 version ID and its time. Read-only."
            input_schema {
              type = "object"
              property {
                name        = "stall"
                type        = "string"
                description = "Stall ID: ${local.stall_list}."
                required    = true
              }
            }
          }
          inline_payload {
            name        = "inspect_recent_changes"
            description = "List recent S3 versions of one stall's menu (newest first, max 20), the price changes between versions, and maintenance-log entries for that menu in the window. States what it could not see. Read-only."
            input_schema {
              type = "object"
              property {
                name        = "stall"
                type        = "string"
                description = "Stall ID: ${local.stall_list}."
                required    = true
              }
              property {
                name        = "hours"
                type        = "integer"
                description = "How far back to look, 1 to 168."
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# --- Harness roles ----------------------------------------------------------------------------

# What any harness needs to run: the pinned model, its managed image, traces and logs.
# Pinned to one model family, so a caller who swaps the model per invoke is denied by IAM.
data "aws_iam_policy_document" "harness_base" {
  statement {
    sid       = "PinnedProfile"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [data.aws_bedrock_inference_profile.model.inference_profile_arn]
  }
  # The model itself, only in the profile's Regions and only when reached through the profile:
  # a caller who swaps modelId or Region per invoke is denied.
  statement {
    sid       = "PinnedModelViaProfile"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = data.aws_bedrock_inference_profile.model.models[*].model_arn
    condition {
      test     = "StringEquals"
      variable = "bedrock:InferenceProfileArn"
      values   = [data.aws_bedrock_inference_profile.model.inference_profile_arn]
    }
  }
  statement {
    sid       = "ManagedImageToken"
    actions   = ["ecr-public:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "ManagedImageBearer"
    actions   = ["sts:GetServiceBearerToken"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "sts:AWSServiceName"
      values   = ["ecr-public.amazonaws.com"]
    }
  }
  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }
  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }
}

# The harness may fetch its own workload token only, never another agent's.
data "aws_iam_policy_document" "workload_identity" {
  statement {
    actions = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT"]
    resources = [
      "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${var.region}:${local.account_id}:workload-identity-directory/default/workload-identity/harness_kopi_investigator-*",
    ]
  }
}

resource "aws_iam_role" "investigator" {
  name               = "kopi-investigator"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

resource "aws_iam_role_policy" "investigator_base" {
  role   = aws_iam_role.investigator.id
  policy = data.aws_iam_policy_document.harness_base.json
}

resource "aws_iam_role_policy" "investigator_identity" {
  role   = aws_iam_role.investigator.id
  policy = data.aws_iam_policy_document.workload_identity.json
}

resource "aws_iam_role_policy" "investigator_gateway" {
  role = aws_iam_role.investigator.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "bedrock-agentcore:InvokeGateway", Resource = aws_bedrockagentcore_gateway.kiosk.gateway_arn }]
  })
}

# --- Harnesses --------------------------------------------------------------------------------

resource "aws_bedrockagentcore_harness" "investigator" {
  harness_name       = "kopi_investigator"
  execution_role_arn = aws_iam_role.investigator.arn
  allowed_tools      = ["@kiosk-tools/*"] # the gateway's tools only; shell and file tools off
  max_iterations     = 10
  timeout_seconds    = 300
  max_tokens         = 4096

  model {
    bedrock_model_config {
      model_id = var.model
    }
  }
  system_prompt {
    text = templatefile("${path.module}/investigator-prompt.md", { stalls = local.stall_list })
  }
  memory {
    disabled {} # each investigation starts clean; no carry-over between runs
  }
  tool {
    type = "agentcore_gateway"
    name = "kiosk-tools"
    config {
      agentcore_gateway {
        gateway_arn = aws_bedrockagentcore_gateway.kiosk.gateway_arn
        outbound_auth {
          aws_iam = true
        }
      }
    }
  }
}
