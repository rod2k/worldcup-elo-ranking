"""
Refresh Elo ratings and update SageMaker endpoint.
Run from notebook:  %run scripts/refresh.py
"""
import json
import tarfile
import time
import uuid
from pathlib import Path

import boto3

SCRIPT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = SCRIPT_DIR / ".deploy_state.json"

if not STATE_PATH.exists():
    print("ERROR: .deploy_state.json not found. Run scripts/deploy.py first.")
    exit(1)

state = json.loads(STATE_PATH.read_text())
BUCKET = state["bucket"]
ENDPOINT_NAME = state["endpoint_name"]
REGION = state.get("region", boto3.session.Session().region_name or "us-east-1")
MODEL_PREFIX = state.get("model_name_prefix", "worldcup-elo-model")
LAMBDA_FN_NAME = state.get("lambda_fn", "worldcup-elo-proxy")

def get_sagemaker_image(region):
    accounts = {
        "af-south-1": "626614931356",
        "ap-east-1": "871362719292",
        "me-south-1": "253153739965",
        "us-gov-west-1": "721309692518",
    }
    acct = accounts.get(region, "763104351884")
    return f"{acct}.dkr.ecr.{region}.amazonaws.com/pytorch-inference:2.1.0-cpu-py310"

SAGEMAKER_IMAGE = get_sagemaker_image(REGION)

s3 = boto3.client("s3", region_name=REGION)
sagemaker = boto3.client("sagemaker", region_name=REGION)
lambda_c = boto3.client("lambda", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)


def step_fetch():
    print("[1/4] Fetching latest Elo ratings...")
    from scripts.fetch_ratings import fetch_ratings

    ratings = fetch_ratings()
    if ratings:
        (SCRIPT_DIR.parent / "ratings.json").write_text(
            json.dumps(ratings, indent=2, ensure_ascii=False)
        )
        print(f"  {len(ratings)} teams saved.")
    else:
        print("  Using existing ratings.json")
    return SCRIPT_DIR.parent / "ratings.json"


def step_rebuild_model():
    print("[2/4] Rebuilding model.tar.gz and uploading...")
    model_name = f"{MODEL_PREFIX}-{uuid.uuid4().hex[:8]}"
    tar_path = SCRIPT_DIR.parent / "model.tar.gz"

    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(str(SCRIPT_DIR.parent / "model" / "inference.py"), arcname="inference.py")
        tar.add(str(SCRIPT_DIR.parent / "model" / "elo_model.py"), arcname="elo_model.py")
        tar.add(str(SCRIPT_DIR.parent / "model" / "requirements.txt"), arcname="requirements.txt")
        tar.add(str(SCRIPT_DIR.parent / "ratings.json"), arcname="ratings.json")

    s3_key = f"models/{tar_path.name}"
    s3.upload_file(str(tar_path), BUCKET, s3_key)
    model_url = f"s3://{BUCKET}/{s3_key}"

    sm_role = iam.get_role(RoleName="WorldCupEloSageMakerRole")["Role"]["Arn"]

    sagemaker.create_model(
        ModelName=model_name,
        PrimaryContainer={
            "Image": SAGEMAKER_IMAGE,
            "Mode": "SingleModel",
            "ModelDataUrl": model_url,
        },
        ExecutionRoleArn=sm_role,
    )

    ec_name = f"{model_name}-ec"
    sagemaker.create_endpoint_config(
        EndpointConfigName=ec_name,
        ProductionVariants=[{
            "VariantName": "AllTraffic",
            "ModelName": model_name,
            "InstanceType": "ml.t3.medium",
            "InitialInstanceCount": 1,
        }],
    )

    sagemaker.update_endpoint(
        EndpointName=ENDPOINT_NAME,
        EndpointConfigName=ec_name,
    )

    return model_name


def step_wait():
    print("[3/4] Waiting for endpoint update...")
    waiter = sagemaker.get_waiter("endpoint_in_service")
    waiter.wait(EndpointName=ENDPOINT_NAME)
    print("  Endpoint updated and InService.")


def step_update_lambda():
    print("[4/4] Ensuring Lambda has correct endpoint name...")
    lambda_c.update_function_configuration(
        FunctionName=LAMBDA_FN_NAME,
        Environment={"Variables": {"ENDPOINT_NAME": ENDPOINT_NAME}},
    )
    print("  Lambda updated.")


if __name__ == "__main__":
    step_fetch()
    step_rebuild_model()
    step_update_lambda()
    step_wait()
    print(f"\n Refresh complete! Endpoint '{ENDPOINT_NAME}' serves updated predictions.")
