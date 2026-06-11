import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elo_model import EloPredictor

_predictor = None


def model_fn(model_dir):
    global _predictor
    ratings_path = os.path.join(model_dir, "ratings.json")
    _predictor = EloPredictor(ratings_path)
    return _predictor


def input_fn(request_body, request_content_type="application/json"):
    return json.loads(request_body)


def predict_fn(input_data, model):
    return model.predict(
        input_data.get("team_a"),
        input_data.get("team_b"),
        neutral=input_data.get("neutral", False),
    )


def output_fn(prediction, response_content_type="application/json"):
    return json.dumps(prediction)
