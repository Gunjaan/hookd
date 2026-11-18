from django.db import migrations

PURPOSES = {"plan": "creative_plan", "render": "creative_photo", "rewrite": "caption_rewrite"}


def copy_calls(apps, schema_editor):
    """Bring the post studio's earlier cost rows over before CreativeAiCall is dropped. What was sent was not kept."""
    CreativeAiCall = apps.get_model("creatives", "CreativeAiCall")
    AiCall = apps.get_model("ai", "AiCall")
    ContentType = apps.get_model("contenttypes", "ContentType")
    if not CreativeAiCall.objects.exists():
        return
    creative_type, _ = ContentType.objects.get_or_create(app_label="creatives", model="creative")
    for old in CreativeAiCall.objects.select_related("creative").iterator():
        new = AiCall.objects.create(
            provider="gemini" if old.model.startswith("gemini") else "claude",
            model=old.model,
            purpose=PURPOSES.get(old.kind, "other"),
            user_id=old.creative.user_id,
            subject_type=creative_type,
            subject_id=old.creative_id,
            input_tokens=old.input_tokens,
            output_tokens=old.output_tokens,
            cost_usd=old.cost_usd,
        )
        AiCall.objects.filter(pk=new.pk).update(created_at=old.created_at, updated_at=old.updated_at)


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0001_initial"),
        ("creatives", "0003_slide_versions_ai_calls"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(copy_calls, migrations.RunPython.noop)]
