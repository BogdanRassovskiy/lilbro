from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0012_processing_draft_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="gen_status",
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
                verbose_name="статус генерации",
            ),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="gen_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="генерация начата"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="gen_finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="генерация завершена"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="gen_error",
            field=models.TextField(blank=True, default="", verbose_name="ошибка генерации"),
        ),
    ]

