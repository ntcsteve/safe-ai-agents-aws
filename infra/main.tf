# The app: a private versioned bucket, served through CloudFront.
# Browsers can read menus/* and site/* only; changes/* (the maintenance log) never leaves AWS.

data "aws_caller_identity" "me" {}

locals {
  scenario = jsondecode(file("${path.module}/../scenario/${var.scenario}.json"))
  # Unique per account and Region without printing the account ID. The Region is in the
  # hash because S3 holds a deleted bucket's name for a while: a Region move must never reuse one.
  bucket = "kopi-${substr(sha1("${data.aws_caller_identity.me.account_id}/${var.region}"), 0, 10)}"
  site_files = {
    "site/index.html" = "text/html; charset=utf-8"
    "site/app.js"     = "text/javascript; charset=utf-8"
    "site/price.js"   = "text/javascript; charset=utf-8"
  }
  promo = local.scenario.promo
}

# --- Bucket -----------------------------------------------------------------------------------

resource "aws_s3_bucket" "menus" {
  bucket = local.bucket
  # Teardown deletes every version. The data is synthetic and `./kopi reset` recreates it.
  force_destroy = true
}

# Versioning is what recovery restores from. No lifecycle rule may expire versions.
resource "aws_s3_bucket_versioning" "menus" {
  bucket = aws_s3_bucket.menus.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "menus" {
  bucket                  = aws_s3_bucket.menus.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "menus" {
  bucket = aws_s3_bucket.menus.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "cloudfront_read" {
  bucket = aws_s3_bucket.menus.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudFrontReadsMenusAndSiteOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = ["${aws_s3_bucket.menus.arn}/menus/*", "${aws_s3_bucket.menus.arn}/site/*"]
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.kiosk.arn } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.menus]
}

# --- Menu page -------------------------------------------------------------------------------

resource "aws_s3_object" "site" {
  for_each      = local.site_files
  bucket        = aws_s3_bucket.menus.id
  key           = each.key
  source        = "${path.module}/../${each.key}"
  etag          = filemd5("${path.module}/../${each.key}")
  content_type  = each.value
  cache_control = "max-age=5"
}

# What the page needs from the scenario: title, stalls in order, promo, and the expected
# prices. Presentation stays out of the menu objects, so the agent and the rollback deal
# in prices only. The page shows the promo badge only while the promo price is live in the menu.
resource "aws_s3_object" "site_config" {
  bucket        = aws_s3_bucket.menus.id
  key           = "site/config.json"
  content_type  = "application/json"
  cache_control = "max-age=5"
  content = jsonencode({
    title  = local.scenario.title
    stalls = keys(local.scenario.stalls)
    # stall -> item -> price: the seed with the approved promo applied, the same rule as
    # kiosk/menu.py expected_prices, so the page and ./kopi check agree on prices.
    expected_prices = {
      for id, stall in local.scenario.stalls : id => merge(
        { for item in stall.items : item.id => item.price },
        id == local.promo.stall ? { (local.promo.item) = local.promo.price } : {}
      )
    }
    promo = {
      item       = local.promo.item
      price      = local.promo.price
      base_price = one([for i in local.scenario.stalls[local.promo.stall].items : i.price if i.id == local.promo.item])
      label      = local.promo.label
    }
  })
}

# --- CloudFront -------------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "menus" {
  name                              = local.bucket
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# A 2-second edge cache: browsers see a change within about 7 s (2 s cache + 5 s poll), while
# many polling browsers cost the origin about one request every 2 s.
resource "aws_cloudfront_cache_policy" "short" {
  name        = "${local.bucket}-short"
  min_ttl     = 0
  default_ttl = 2
  max_ttl     = 5
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "kiosk" {
  enabled             = true
  comment             = "Kopi Kiosk"
  default_root_object = "site/index.html"
  price_class         = "PriceClass_200" # includes Asia

  origin {
    origin_id                = "menus"
    domain_name              = aws_s3_bucket.menus.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.menus.id
  }

  default_cache_behavior {
    target_origin_id           = "menus"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.short.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
