from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0019_agent_and_item_pause_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_delay_min_minutes",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="минимальная задержка ответа (мин)"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="reply_delay_max_minutes",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="максимальная задержка ответа (мин)"),
        ),
    ]

