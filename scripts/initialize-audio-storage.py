"""Create an absent development bucket and fail closed on public policies/ACLs."""

import boto3
from app.config import get_settings
from botocore.exceptions import ClientError

settings = get_settings()
if settings.environment == "production" or settings.object_storage_backend != "s3":
    raise SystemExit(
        "This bootstrap requires explicit non-production S3 configuration."
    )
client = boto3.client(
    "s3",
    endpoint_url=settings.object_storage_endpoint,
    aws_access_key_id=settings.object_storage_access_key,
    aws_secret_access_key=settings.object_storage_secret_key,
    region_name=settings.object_storage_region,
)
bucket = settings.object_storage_bucket
try:
    client.head_bucket(Bucket=bucket)
except ClientError as error:
    if error.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
        raise SystemExit("Storage unavailable or credentials refused.") from None
    client.create_bucket(Bucket=bucket)
try:
    client.get_bucket_policy(Bucket=bucket)
except ClientError as error:
    if error.response["Error"]["Code"] not in {
        "NoSuchBucketPolicy",
        "NoSuchPolicy",
        "404",
    }:
        raise SystemExit("Could not verify private bucket policy.") from None
else:
    raise SystemExit(
        "Bucket has a policy; private development bootstrap requires an unshared bucket."
    )
for grant in client.get_bucket_acl(Bucket=bucket)["Grants"]:
    if grant["Grantee"].get("Type") == "Group":
        raise SystemExit(
            "Bucket has a group ACL; refusing to use public/shared audio storage."
        )
print(
    "Private bucket exists, credentials verified, anonymous policy absent; no objects deleted."
)
