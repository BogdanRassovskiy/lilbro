from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0022_default_reply_delay_to_0_0"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="eval_stage",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="этап оценки"),
        ),
    ]

