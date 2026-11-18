"""Record of every AI provider call.

The provider clients (`claude.py`, `gemini.py`) call `record` once per request, whether it worked or not. Who and what
the call was for comes from `attributed`, which the feature services wrap around their engine calls, so engines and
their interfaces stay unaware of it.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.ai import model_querysets as queries
from apps.ai.constants import CallStatus, Purpose

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attribution:
    purpose: str = Purpose.OTHER
    user: object = None
    account: object = None
    subject: models.Model | None = None


_attribution: ContextVar[Attribution] = ContextVar("ai_attribution", default=Attribution())


@contextmanager
def attributed(purpose: str, *, user=None, account=None, subject: models.Model | None = None):
    """Every AI call made inside is recorded with this purpose, user, account and subject. The user defaults to the
    account's owner."""
    if user is None and account is not None:
        user = account.user
    token = _attribution.set(Attribution(purpose=purpose, user=user, account=account, subject=subject))
    try:
        yield
    finally:
        _attribution.reset(token)


def call_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """USD at AI_MODEL_PRICES (per million tokens). A model without a price costs 0 and is logged."""
    price = settings.AI_MODEL_PRICES.get(model)
    if price is None:
        logger.warning("No price for AI model %s, its cost is recorded as 0.", model)
        return Decimal("0")
    cost = (Decimal(str(price["input"])) * input_tokens + Decimal(str(price["output"])) * output_tokens) / 1_000_000
    return cost.quantize(Decimal("0.000001"))


def media_summary(mime_type: str, data: bytes) -> dict:
    """What is stored instead of an image: its type and size."""
    return {"type": "image", "media_type": mime_type, "bytes": len(data)}


def record(
    *, provider: str, model: str, system: str = "", request: dict, response=None, input_tokens: int = 0,
    output_tokens: int = 0, duration_ms: int = 0, error: str = "",
) -> None:
    """Store one provider call. A failure to store is logged and never breaks the AI call itself."""
    scope = _attribution.get()
    try:
        queries.add_call(
            provider=provider,
            model=model,
            purpose=scope.purpose,
            status=CallStatus.FAILED if error else CallStatus.OK,
            error=error,
            user=scope.user,
            account=scope.account,
            subject=scope.subject,
            system=system,
            request=request,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=call_cost(model, input_tokens, output_tokens),
            duration_ms=duration_ms,
        )
    except Exception:
        logger.exception("Could not record a %s call to %s.", provider, model)
