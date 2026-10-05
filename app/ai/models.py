import os

from openai import OpenAI

from app.ai.model_catalog import MODEL_CATALOG
from app.ai.account_limits import ACCOUNT_LIMITS
from app.ai.model_scoring import score_models


MINIMUM_MODEL_RANK = 5.5


def get_client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")

    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured"
        )

    return OpenAI(api_key=key)


def available_models() -> list[dict]:
    client = get_client()

    response = client.models.list()

    available_ids = {
        model.id
        for model in response.data
    }

    models = []

    for model_id, catalog in MODEL_CATALOG.items():
        if model_id not in available_ids:
            continue

        if catalog["family_rank"] < MINIMUM_MODEL_RANK:
            continue

        limits = ACCOUNT_LIMITS.get(
            model_id,
            {},
        )

        models.append({
            "id": model_id,

            "family_rank":
                catalog["family_rank"],

            "input":
                catalog["input"],

            "cached_input":
                catalog["cached_input"],

            "output":
                catalog["output"],

            "tpm":
                limits.get(
                    "tpm",
                    catalog.get("tpm"),
                ),

            "tpd":
                limits.get(
                    "tpd",
                    catalog.get("tpd"),
                ),
        })

    scored = score_models(models)

    return sorted(
        scored,
        key=lambda m: (
            m["affordability_score"]
            if m["affordability_score"] is not None
            else -1
        ),
        reverse=True,
    )

