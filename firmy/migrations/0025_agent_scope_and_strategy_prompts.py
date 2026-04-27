from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0024_processing_draft_requires_confirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyagent",
            name="prompt_scope",
            field=models.TextField(blank=True, default="", verbose_name="промпт: разрешенные темы и вопросы"),
        ),
        migrations.AddField(
            model_name="firmyagent",
            name="prompt_strategy",
            field=models.TextField(blank=True, default="", verbose_name="промпт: стратегия и тактика ответа"),
        ),
    ]
