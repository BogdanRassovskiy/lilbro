from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0017_processing_lead_traits"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_status",
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
                verbose_name="статус автоответа",
            ),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="автоответ начат"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="автоответ завершен"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_error",
            field=models.TextField(blank=True, default="", verbose_name="ошибка автоответа"),
        ),
    ]

