from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0010_processing_assigned_agent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="firmyprocessingitem",
            name="premise",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="processing_items",
                to="firmy.FirmyPremise",
                verbose_name="карточка",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="firmyprocessingitem",
            unique_together={("premise", "assigned_agent")},
        ),
    ]

