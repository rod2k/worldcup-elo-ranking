import json
import os

import boto3

sagemaker = boto3.client("sagemaker-runtime")
ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]


def handler(event, context):
    body = json.loads(event.get("body", "{}"))
    response = sagemaker.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/json",
        Body=json.dumps(body),
    )
    result = json.loads(response["Body"].read().decode())
    return {
        "statusCode": 200,
        "headers": {"Access-Control-Allow-Origin": "*"},
        "body": json.dumps(result),
    }
