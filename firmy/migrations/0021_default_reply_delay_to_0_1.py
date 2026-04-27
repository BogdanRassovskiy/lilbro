from django.db import migrations, models


def backfill_reply_delay(apps, schema_editor):
    FirmyProcessingItem = apps.get_model("firmy", "FirmyProcessingItem")
    FirmyProcessingItem.objects.filter(
        reply_delay_min_minutes=0,
        reply_delay_max_minutes=0,
    ).update(reply_delay_max_minutes=1)


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0020_processing_reply_delay_range"),
    ]

    operations = [
        migrations.AlterField(
            model_name="firmyprocessingitem",
            name="reply_delay_max_minutes",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="максимальная задержка ответа (мин)"),
        ),
        migrations.RunPython(backfill_reply_delay, migrations.RunPython.noop),
    ]

