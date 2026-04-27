from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0008_processing_do_not_contact"),
    ]

    operations = [
        migrations.CreateModel(
            name="FirmyAgent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="имя")),
                ("avatar", models.CharField(blank=True, default="👤", max_length=8, verbose_name="аватар")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="email")),
                ("phone", models.CharField(blank=True, max_length=40, verbose_name="номер телефона")),
                ("model_name", models.CharField(blank=True, max_length=120, verbose_name="название модели")),
                ("token_limit", models.PositiveIntegerField(default=0, verbose_name="лимит токенов")),
                (
                    "role",
                    models.CharField(
                        choices=[("searcher", "поисковик"), ("evaluator", "оценщик"), ("interviewer", "собеседник")],
                        default="searcher",
                        max_length=24,
                        verbose_name="роль",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="обновлено")),
            ],
            options={
                "ordering": ("name", "id"),
                "verbose_name": "агент",
                "verbose_name_plural": "агенты",
            },
        ),
    ]

