from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0018_processing_auto_reply_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyagent",
            name="processing_enabled",
            field=models.BooleanField(default=True, verbose_name="автопроцессинг включен"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="paused_individual",
            field=models.BooleanField(default=False, verbose_name="чат на паузе индивидуально"),
        ),
    ]

