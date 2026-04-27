from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0015_agent_system_prompt"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="evaluation_text",
            field=models.TextField(blank=True, default="", verbose_name="оценка (текст)"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="eval_status",
            field=models.CharField(
                choices=[
                    ("idle", "не запущено"),
                    ("running", "в процессе"),
                    ("done", "готово"),
                    ("error", "ошибка"),
                ],
                db_index=True,
                default="idle",
                max_length=16,
                verbose_name="статус оценки",
            ),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="eval_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="оценка начата"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="eval_finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="оценка завершена"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="eval_error",
            field=models.TextField(blank=True, default="", verbose_name="ошибка оценки"),
        ),
    ]

