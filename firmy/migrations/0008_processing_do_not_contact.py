from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0007_alter_firmyprocessingitem_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="do_not_contact",
            field=models.BooleanField(default=False, verbose_name="больше не писать"),
        ),
    ]

