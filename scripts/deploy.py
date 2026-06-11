"""
Full deployment from SageMaker Notebook.
Creates: S3 bucket, IAM roles, Lambda, API Gateway, SageMaker endpoint, static UI.
Run once:  %run scripts/deploy.py
"""
import json
import os
import tarfile
import time
import zipfile
import uuid
from io import BytesIO
from pathlib import Path

import boto3

SCRIPT_DIR = Path(__file__).resolve().parent.parent
AWS_REGION = boto3.session.Session().region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]
SUFFIX = uuid.uuid4().hex[:8]

# ── Config ──────────────────────────────────────────────────────────
BUCKET = f"worldcup-elo-{SUFFIX}"
MODEL_NAME = f"worldcup-elo-model-{SUFFIX}"
ENDPOINT_CONFIG_NAME = f"worldcup-elo-ec-{SUFFIX}"
ENDPOINT_NAME = "worldcup-elo-endpoint"
INSTANCE_TYPE = "ml.t3.medium"
LAMBDA_FN_NAME = "worldcup-elo-proxy"
API_NAME = "worldcup-elo-api"

def get_sagemaker_image(region):
    accounts = {
        "af-south-1": "626614931356",
        "ap-east-1": "871362719292",
        "me-south-1": "253153739965",
        "us-gov-west-1": "721309692518",
    }
    acct = accounts.get(region, "763104351884")
    return f"{acct}.dkr.ecr.{region}.amazonaws.com/pytorch-inference:2.1.0-cpu-py310"

SAGEMAKER_IMAGE = get_sagemaker_image(AWS_REGION)

# ── Clients ─────────────────────────────────────────────────────────
s3 = boto3.client("s3", region_name=AWS_REGION)
iam = boto3.client("iam", region_name=AWS_REGION)
sagemaker = boto3.client("sagemaker", region_name=AWS_REGION)
lambda_c = boto3.client("lambda", region_name=AWS_REGION)
apigw = boto3.client("apigatewayv2", region_name=AWS_REGION)


# ──── 1. S3 Bucket ──────────────────────────────────────────────────
def create_s3_bucket():
    print("[1/6] Creating S3 bucket...")
    kwargs = {"Bucket": BUCKET}
    if AWS_REGION != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": AWS_REGION}
    try:
        s3.create_bucket(**kwargs)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass

    s3.put_bucket_website(
        Bucket=BUCKET,
        WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}},
    )
    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False,
            "IgnorePublicAcls": False,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
    s3.put_bucket_policy(
        Bucket=BUCKET,
        Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/*",
            }],
        }),
    )
    return BUCKET


# ──── 2. IAM Roles ──────────────────────────────────────────────────
def get_or_create_role(role_name, assume_role_policy, policy_arns):
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
        )
        for arn in policy_arns:
            try:
                iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)
            except iam.exceptions.LimitExceededException:
                pass
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)
    return role["Role"]["Arn"]


def create_iam_roles():
    print("[2/6] Creating IAM roles...")

    sm_role_arn = get_or_create_role(
        "WorldCupEloSageMakerRole",
        {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "sagemaker.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        },
        [
            "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        ],
    )

    lambda_role_arn = get_or_create_role(
        "WorldCupEloLambdaRole",
        {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        },
        [
            "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess",
        ],
    )

    return sm_role_arn, lambda_role_arn


# ──── 3. Lambda Function ────────────────────────────────────────────
def create_lambda_function(lambda_role_arn):
    print("[3/6] Creating Lambda function...")
    handler_path = SCRIPT_DIR / "lambda" / "handler.py"
    code = handler_path.read_text()

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("handler.py", code)
    buf.seek(0)

    try:
        resp = lambda_c.create_function(
            FunctionName=LAMBDA_FN_NAME,
            Runtime="python3.13",
            Role=lambda_role_arn,
            Handler="handler.handler",
            Code={"ZipFile": buf.read()},
            Environment={"Variables": {"ENDPOINT_NAME": ENDPOINT_NAME}},
            Timeout=30,
        )
        return resp["FunctionArn"]
    except lambda_c.exceptions.ResourceConflictException:
        lambda_c.update_function_code(
            FunctionName=LAMBDA_FN_NAME,
            ZipFile=buf.getvalue(),
        )
        resp = lambda_c.get_function(FunctionName=LAMBDA_FN_NAME)
        return resp["Configuration"]["FunctionArn"]


# ──── 4. API Gateway ────────────────────────────────────────────────
def create_api_gateway(lambda_arn):
    print("[4/6] Creating API Gateway...")
    try:
        api = apigw.create_api(
            Name=API_NAME,
            ProtocolType="HTTP",
            Target=lambda_arn,
        )
    except apigw.exceptions.ConflictException:
        apis = apigw.get_apis()["Items"]
        api = next(a for a in apis if a["Name"] == API_NAME)

    apigw.update_api(
        ApiId=api["ApiId"],
        CorsConfiguration={
            "AllowOrigins": ["*"],
            "AllowMethods": ["POST", "OPTIONS"],
            "AllowHeaders": ["Content-Type"],
        },
    )

    api_url = api["ApiEndpoint"]

    try:
        lambda_c.add_permission(
            FunctionName=LAMBDA_FN_NAME,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{AWS_REGION}:{ACCOUNT_ID}:{api['ApiId']}/*/*",
        )
    except lambda_c.exceptions.ResourceConflictException:
        pass

    return api_url


# ──── 5. SageMaker Model + Endpoint ─────────────────────────────────
def deploy_sagemaker_model(sm_role_arn, api_url):
    print("[5/6] Deploying SageMaker model...")

    tar_path = SCRIPT_DIR / "model.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(str(SCRIPT_DIR / "model" / "inference.py"), arcname="inference.py")
        tar.add(str(SCRIPT_DIR / "model" / "elo_model.py"), arcname="elo_model.py")
        tar.add(str(SCRIPT_DIR / "model" / "requirements.txt"), arcname="requirements.txt")
        tar.add(str(SCRIPT_DIR / "ratings.json"), arcname="ratings.json")

    s3_key = f"models/{tar_path.name}"
    s3.upload_file(str(tar_path), BUCKET, s3_key)
    model_url = f"s3://{BUCKET}/{s3_key}"

    try:
        sagemaker.create_model(
            ModelName=MODEL_NAME,
            PrimaryContainer={
                "Image": SAGEMAKER_IMAGE,
                "Mode": "SingleModel",
                "ModelDataUrl": model_url,
            },
            ExecutionRoleArn=sm_role_arn,
        )
    except sagemaker.exceptions.ResourceInUse:
        pass

    try:
        sagemaker.create_endpoint_config(
            EndpointConfigName=ENDPOINT_CONFIG_NAME,
            ProductionVariants=[{
                "VariantName": "AllTraffic",
                "ModelName": MODEL_NAME,
                "InstanceType": INSTANCE_TYPE,
                "InitialInstanceCount": 1,
            }],
        )
    except sagemaker.exceptions.ResourceInUse:
        pass

    try:
        sagemaker.create_endpoint(
            EndpointName=ENDPOINT_NAME,
            EndpointConfigName=ENDPOINT_CONFIG_NAME,
        )
    except sagemaker.exceptions.ResourceInUse:
        sagemaker.update_endpoint(
            EndpointName=ENDPOINT_NAME,
            EndpointConfigName=ENDPOINT_CONFIG_NAME,
        )

    print("  Waiting for endpoint to be InService (5-8 min)...")
    waiter = sagemaker.get_waiter("endpoint_in_service")
    waiter.wait(EndpointName=ENDPOINT_NAME)
    print(f"  Endpoint {ENDPOINT_NAME} is ready.")

    lambda_c.update_function_configuration(
        FunctionName=LAMBDA_FN_NAME,
        Environment={"Variables": {"ENDPOINT_NAME": ENDPOINT_NAME}},
    )

    return api_url


# ──── 6. Deploy UI ──────────────────────────────────────────────────
def deploy_ui(api_url):
    print("[6/6] Deploying static UI to S3...")
    html = (SCRIPT_DIR / "ui" / "index.html").read_text().replace("$API_URL", api_url)
    s3.put_object(Bucket=BUCKET, Key="index.html", Body=html, ContentType="text/html")
    website_url = f"http://{BUCKET}.s3-website-{AWS_REGION}.amazonaws.com"
    print(f"  UI available at: {website_url}")
    return website_url


# ──── Save state ────────────────────────────────────────────────────
def save_state(api_url, ui_url):
    state = {
        "bucket": BUCKET,
        "endpoint_name": ENDPOINT_NAME,
        "model_name_prefix": "worldcup-elo-model",
        "region": AWS_REGION,
        "lambda_fn": LAMBDA_FN_NAME,
        "api_url": api_url,
        "ui_url": ui_url,
    }
    (SCRIPT_DIR / ".deploy_state.json").write_text(json.dumps(state, indent=2))


# ──── Run ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    bucket = create_s3_bucket()
    sm_role_arn, lambda_role_arn = create_iam_roles()
    lambda_arn = create_lambda_function(lambda_role_arn)
    api_url = create_api_gateway(lambda_arn)
    api_url = deploy_sagemaker_model(sm_role_arn, api_url)
    ui_url = deploy_ui(api_url)
    save_state(api_url, ui_url)

    print(f"\n{'='*55}")
    print("  DEPLOYMENT COMPLETE")
    print(f"{'='*55}")
    print(f"  UI:       {ui_url}")
    print(f"  API:      {api_url}")
    print(f"  Endpoint: {ENDPOINT_NAME}")
    print(f"  Bucket:   s3://{bucket}")
    print(f"{'='*55}")
