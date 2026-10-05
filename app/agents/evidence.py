import json
import re
from typing import Any


def normalise_evidence(
    evidence: list[dict] | None,
) -> list[dict]:
    cleaned = []

    for index, item in enumerate(evidence or []):
        if not isinstance(item, dict):
            continue

        ref = str(
            item.get("ref")
            or item.get("id")
            or f"evidence-{index + 1}"
        )

        cleaned.append({
            "ref": ref,
            "source": str(
                item.get("source") or "user_supplied"
            ),
            "content": str(
                item.get("content") or ""
            )[:12000],
        })

    return cleaned


def extract_json_object(
    text: str,
) -> dict[str, Any]:
    raw = (text or "").strip()
    fence = chr(96) * 3

    if raw.startswith(fence):
        raw = re.sub(
            r"^" + re.escape(fence) + r"(?:json)?\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        raw = re.sub(
            r"\s*" + re.escape(fence) + r"$",
            "",
            raw,
        )

    try:
        value = json.loads(raw)

        if isinstance(value, dict):
            return value

    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")

    if start >= 0 and end > start:
        candidate = raw[start:end + 1]

        try:
            value = json.loads(candidate)

            if isinstance(value, dict):
                return value

        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Agent did not return valid JSON"
    )


def deterministic_concerns(
    *,
    output: dict,
    evidence: list[dict],
) -> list[dict]:
    concerns = []

    known_refs = {
        str(item["ref"])
        for item in evidence
    }

    facts = output.get("facts") or []

    for fact in facts:
        if not isinstance(fact, dict):
            continue

        claim = str(
            fact.get("claim") or ""
        )

        refs = {
            str(ref)
            for ref in (
                fact.get("evidence_refs") or []
            )
        }

        missing = refs - known_refs

        if not refs:
            concerns.append({
                "severity": "warning",
                "category": "unsupported_fact",
                "message": (
                    "Claim is presented as fact without "
                    f"an evidence reference: {claim[:300]}"
                ),
                "evidence_refs": [],
            })

        if missing:
            concerns.append({
                "severity": "warning",
                "category": "unknown_evidence_reference",
                "message": (
                    "Claim references evidence that was "
                    "not supplied to the run."
                ),
                "evidence_refs": sorted(missing),
            })

        confidence = fact.get(
            "confidence"
        )

        if isinstance(
            confidence,
            (int, float),
        ):
            if confidence > 0.85 and not refs:
                concerns.append({
                    "severity": "warning",
                    "category": "unsupported_confidence",
                    "message": (
                        "High-confidence fact has no "
                        "evidence reference."
                    ),
                    "evidence_refs": [],
                })

    recommendations = (
        output.get("recommendations") or []
    )

    for recommendation in recommendations:
        if not isinstance(
            recommendation,
            dict,
        ):
            continue

        risk = str(
            recommendation.get("risk")
            or "read"
        )

        approval = recommendation.get(
            "requires_human_approval"
        )

        if (
            risk in {
                "safe_write",
                "privileged_write",
                "destructive",
            }
            and approval is not True
        ):
            concerns.append({
                "severity": "critical",
                "category": "unsafe_recommendation",
                "message": (
                    "Write-capable recommendation did "
                    "not explicitly require human approval."
                ),
                "evidence_refs": [],
            })

    if not evidence and facts:
        concerns.append({
            "severity": "warning",
            "category": "weak_evidence_basis",
            "message": (
                "The run produced factual claims without "
                "any supplied evidence packet."
            ),
            "evidence_refs": [],
        })

    return concerns
