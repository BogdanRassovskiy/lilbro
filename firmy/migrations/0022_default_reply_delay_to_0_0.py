from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0021_default_reply_delay_to_0_1"),
    ]

    operations = [
        migrations.AlterField(
            model_name="firmyprocessingitem",
            name="reply_delay_max_minutes",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="максимальная задержка ответа (мин)"),
        ),
    ]

