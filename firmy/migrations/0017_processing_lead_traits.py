from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0016_processing_evaluation_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="lead_state",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="состояние лида"),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="response_type",
            field=models.TextField(
                blank=True,
                default="[]",
                help_text='JSON-массив, например: ["no_response","interested"]',
                verbose_name="тип ответа (JSON массив)",
            ),
        ),
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="communication_style",
            field=models.TextField(
                blank=True,
                default="[]",
                help_text='JSON-массив, например: ["short","formal"]',
                verbose_name="стиль коммуникации (JSON массив)",
            ),
        ),
    ]

