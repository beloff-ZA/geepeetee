import math


def normalize_cost(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0

    return (value - low) / (high - low)


def normalize_capacity(value: int, low: int, high: int) -> float:
    if high <= low:
        return 1.0

    return (value - low) / (high - low)


def score_models(models: list[dict]) -> list[dict]:
    priced = [
        m for m in models
        if m.get("input") is not None
        and m.get("output") is not None
    ]

    if not priced:
        return models

    input_values = [m["input"] for m in priced]
    cache_values = [
        m["cached_input"]
        for m in priced
        if m.get("cached_input") is not None
    ]
    output_values = [m["output"] for m in priced]

    tpm_values = [
        m["tpm"]
        for m in priced
        if m.get("tpm") is not None
    ]

    tpd_values = [
        m["tpd"]
        for m in priced
        if m.get("tpd") is not None
    ]

    for model in models:
        if (
            model.get("input") is None
            or model.get("output") is None
        ):
            model["affordability_score"] = None
            model["colour"] = "#808080"
            continue

        input_cost = normalize_cost(
            model["input"],
            min(input_values),
            max(input_values),
        )

        output_cost = normalize_cost(
            model["output"],
            min(output_values),
            max(output_values),
        )

        if (
            model.get("cached_input") is not None
            and cache_values
        ):
            cache_cost = normalize_cost(
                model["cached_input"],
                min(cache_values),
                max(cache_values),
            )
        else:
            cache_cost = input_cost

        cost_score = (
            input_cost * 0.30
            + cache_cost * 0.15
            + output_cost * 0.35
        )

        capacity_penalty = 0.0

        if model.get("tpm") is not None and len(tpm_values) > 1:
            capacity_penalty += (
                1.0
                - normalize_capacity(
                    model["tpm"],
                    min(tpm_values),
                    max(tpm_values),
                )
            ) * 0.10

        if model.get("tpd") is not None and len(tpd_values) > 1:
            capacity_penalty += (
                1.0
                - normalize_capacity(
                    model["tpd"],
                    min(tpd_values),
                    max(tpd_values),
                )
            ) * 0.10

        expense = min(
            1.0,
            cost_score + capacity_penalty,
        )

        affordability = round(
            (1.0 - expense) * 100,
            1,
        )

        model["affordability_score"] = affordability

        # Hue:
        # 120 = green
        # 60  = yellow
        # 0   = red

        hue = round(120 * (affordability / 100))

        model["colour"] = f"hsl({hue}, 75%, 45%)"

    return models
