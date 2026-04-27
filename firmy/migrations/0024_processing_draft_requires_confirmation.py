from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0023_processing_eval_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="draft_requires_confirmation",
            field=models.BooleanField(default=False, verbose_name="черновик требует подтверждения"),
        ),
    ]

