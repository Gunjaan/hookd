"""All database access for AI call records lives here."""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import F, OuterRef, QuerySet, Subquery, Sum
from django.db.models.functions import Coalesce

from apps.ai.models import AiCall


def add_call(**fields) -> AiCall:
    # A savepoint: a failed insert never breaks the caller's transaction.
    with transaction.atomic():
        return AiCall.objects.create(**fields)


def usage_totals(queryset: QuerySet[AiCall]) -> dict:
    """Tokens and cost over a set of calls (admin changelist totals)."""
    return queryset.aggregate(
        input_tokens=Coalesce(Sum("input_tokens"), 0),
        output_tokens=Coalesce(Sum("output_tokens"), 0),
        cost=Coalesce(Sum("cost_usd"), Decimal("0")),
    )


def with_usage_totals(queryset: QuerySet) -> QuerySet:
    """Annotate rows of any model with `total_tokens` and `total_cost` over the calls made about them (admin lists)."""
    calls = (
        AiCall.objects.filter(subject_type=ContentType.objects.get_for_model(queryset.model), subject_id=OuterRef("pk"))
        .order_by()
        .values("subject_id")
    )
    tokens = calls.annotate(total=Sum(F("input_tokens") + F("output_tokens"))).values("total")
    cost = calls.annotate(total=Sum("cost_usd")).values("total")
    return queryset.annotate(
        total_tokens=Coalesce(Subquery(tokens, output_field=models.IntegerField()), 0),
        total_cost=Coalesce(Subquery(cost, output_field=models.DecimalField(max_digits=12, decimal_places=6)), Decimal("0")),
    )


def calls_about(obj: models.Model) -> QuerySet[AiCall]:
    return AiCall.objects.filter(subject_type=ContentType.objects.get_for_model(obj), subject_id=obj.pk)
