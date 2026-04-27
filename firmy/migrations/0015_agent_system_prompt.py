from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0014_alter_firmyprocessingitem_queued_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyagent",
            name="system_prompt",
            field=models.TextField(blank=True, default="", verbose_name="промпт (кто я и задача)"),
        ),
    ]

