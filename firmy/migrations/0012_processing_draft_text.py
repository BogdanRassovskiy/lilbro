from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0011_processing_per_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="draft_text",
            field=models.TextField(blank=True, default="", verbose_name="черновик сообщения"),
        ),
    ]

