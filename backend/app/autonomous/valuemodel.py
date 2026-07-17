"""Value modeling — prove the idea is worth money by pricing the STATUS QUO.

Every other lens grades the market (is demand real? is the moat durable?) or the
founder (can they attack it?). None of them answer the question a buyer actually
asks in the first meeting: *what does this save me, in dollars, over how I do the
job today?* A gap can survive the whole gauntlet and still be un-sellable because
the pain it removes was never expensive enough to pay for.

This module makes that case explicit, the way a rigorous value analyst would. It
uses the Jobs-To-Be-Done + workflow-waste method:

* reconstruct the STATUS-QUO workflow the gap attacks — how the job is actually
  done today, step by step;
* price that workflow — time per step, loaded labor rate, error/rework cost —
  with every assumption stated out loud;
* isolate the fraction this gap can actually remove (the automatable / avoidable
  part), and turn it into a defensible ANNUAL dollar figure for one typical
  buyer, WITH the arithmetic shown, not hand-waved.

The point is honesty, not boosterism: a small number, clearly derived, is worth
far more than a big number nobody can trace. When the recoverable spend is thin,
the model says so — that is signal, not failure.

One strong-model call. It degrades like every operator here: a fixture backend,
any exception, or unparseable output yields ``None``. A failed value model must
never break a run.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..analysis.synthesize import _extract_json_array, _strip_fences
from ..llm.client import ClaudeClient
from ..schemas import Gap
from .intake import steering_context_block
from .pressure import _gap_payload

logger = logging.getLogger("gapfinder.valuemodel")


class ValueModel(BaseModel):
    """A costed case for one typical buyer: what the status quo costs, and the
    defensible annual ROI this gap removes from it — with the formula shown."""

    status_quo: str                                    # how the job is done TODAY, concretely
    current_cost: str                                  # time/money/error cost of that, quantified
    cost_drivers: list[str] = Field(default_factory=list)   # the 2-4 biggest cost drivers
    solution_delta: str                                # how THIS gap reduces that cost
    annual_value: str                                  # $ / typical buyer + the formula
    assumptions: list[str] = Field(default_factory=list)    # what the number rests on
    confidence: str = "low"                            # "low" | "medium" | "high"


_SYSTEM = """\
You are a rigorous business-value analyst. You are given ONE market-gap idea and
must model, in hard numbers, what its target buyer's STATUS QUO costs today and
how much of that cost this idea actually removes. Use the Jobs-To-Be-Done +
workflow-waste method, in this order:

1. STATUS QUO — reconstruct how the job is done TODAY, concretely. Name the real
   steps, the tools/people involved, and where the waste lives. No vague "manual
   processes"; describe the actual workflow this idea attacks.
2. COST OF THE STATUS QUO — price it: time per step × frequency × a loaded labor
   rate, plus error/rework/opportunity cost where they dominate. State the rate
   and volume you assume. Quantify — don't say "expensive".
3. COST DRIVERS — the 2-4 line items that actually move the total.
4. SOLUTION DELTA — how THIS specific idea reduces that cost, and which fraction
   of the workflow it can realistically automate or avoid (be honest: rarely
   100%).
5. ANNUAL VALUE — a single dollar figure of recoverable spend for ONE typical
   buyer per year, and SHOW THE FORMULA that produces it
   (e.g. "6 hrs/wk saved × $75 loaded/hr × 48 wks × 0.7 adoption ≈ $15,100/yr").
   The number must follow arithmetically from your stated assumptions.

HARD RULES:
- Demand a CONCRETE number with its formula. A value model without arithmetic is
  a failure.
- Be HONEST when the value is small. A thin, well-derived number is far more
  useful than an inflated one; say plainly when the pain isn't expensive enough
  to pay for. Set "confidence" to reflect how solid the inputs are, not how big
  the number is.
- Reason ONLY from the idea and any founder steering given. Do not invent
  citations or market-size statistics; derive from the workflow.

OUTPUT CONTRACT — STRICT
Return ONLY a JSON object (no prose, no markdown fences), EXACTLY these keys:
{
  "status_quo": "how the job is done today, concretely",
  "current_cost": "quantified time/money/error cost of the status quo",
  "cost_drivers": ["the 2-4 biggest cost drivers"],
  "solution_delta": "how THIS idea reduces that cost, and the fraction it removes",
  "annual_value": "$ estimate for one typical buyer WITH the formula shown",
  "assumptions": ["each assumption the number rests on"],
  "confidence": "low | medium | high"
}\
"""


def _prompt(gap: Gap, steering: str) -> str:
    import json

    return (
        (f"FOUNDER STEERING CONTEXT (ground the buyer/rate assumptions in this "
         f"where it applies):\n{steering}\n\n" if steering else "")
        + "Model the value of THIS idea — price the status quo it attacks, then "
        "derive the annual recoverable spend for one typical buyer WITH the "
        "formula shown:\n\n"
        + json.dumps(_gap_payload(gap), indent=2)
        + "\n\nReturn ONLY the strict JSON object."
    )


async def model_value(
    gap: Gap, project: Any, client: ClaudeClient, model: str
) -> Optional[ValueModel]:
    """Model the status-quo cost this gap attacks and its defensible annual ROI.

    One strong-model pass → a validated :class:`ValueModel`, or ``None``. NEVER
    raises: a fixture backend, any LLM/parse error, or output that won't validate
    all degrade to ``None`` so a bad value model can't break a run.
    """
    steering = steering_context_block(project) or ""
    try:
        result = await client.complete(
            _prompt(gap, steering),
            system=_SYSTEM,
            model=model,
            max_turns=1,
            timeout=120,
        )
    except Exception:  # noqa: BLE001 - a failed value model must never break a run
        return None
    if getattr(result, "backend", "") == "fixture":
        return None

    obj = _parse_object(result.text or "")
    if obj is None:
        return None
    try:
        return ValueModel.model_validate(obj)
    except Exception:  # noqa: BLE001 - unparseable/invalid shape degrades to None
        return None


def _parse_object(text: str) -> Optional[dict]:
    """Pull the ONE JSON object the contract asks for out of model output.

    The contract is a single object, but the shared ``_extract_json_array`` is
    array-shaped (and its bracket-scan fallback would grab an INNER list like
    ``cost_drivers`` from a bare object). So: try a lenient object parse first —
    whole text, then fence-stripped, then the outermost ``{...}`` span — and only
    fall back to the array extractor for models that wrap the object in ``[...]``
    or a ``{"...": [...]}`` envelope.
    """
    if not text:
        return None
    for cand in (text.strip(), _strip_fences(text).strip()):
        if not cand:
            continue
        try:
            got = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(got, dict):
            return got
        if isinstance(got, list) and got and isinstance(got[0], dict):
            return got[0]
    # Salvage the outermost {...} span from surrounding prose.
    src = _strip_fences(text)
    start, end = src.find("{"), src.rfind("}")
    if 0 <= start < end:
        try:
            got = json.loads(src[start : end + 1])
            if isinstance(got, dict):
                return got
        except json.JSONDecodeError:
            pass
    # Last resort: an array-wrapped or enveloped object.
    arr = _extract_json_array(text)
    if arr and isinstance(arr[0], dict):
        return arr[0]
    return None
