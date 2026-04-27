from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0009_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="assigned_agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="processing_items",
                to="firmy.FirmyAgent",
                verbose_name="закрепленный собеседник",
            ),
        ),
    ]

