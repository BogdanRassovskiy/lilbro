from django.db import migrations, models
import django.db.models.deletion


def forwards_copy_legacy(apps, schema_editor):
    FirmySearchResult = apps.get_model("firmy", "FirmySearchResult")
    FirmyPremise = apps.get_model("firmy", "FirmyPremise")
    FirmySearchHit = apps.get_model("firmy", "FirmySearchHit")

    for r in FirmySearchResult.objects.all().iterator():
        p, created = FirmyPremise.objects.get_or_create(
            premise_id=r.premise_id,
            defaults={
                "title": r.title,
                "detail_url": r.detail_url,
                "category": r.category,
                "address": r.address,
                "card_text": r.card_text,
                "phones": getattr(r, "phones", "") or "",
                "emails": getattr(r, "emails", "") or "",
                "website_url": getattr(r, "website_url", "") or "",
            },
        )
        if not created:
            changed = False
            for field in (
                "title",
                "detail_url",
                "category",
                "address",
                "card_text",
                "phones",
                "emails",
                "website_url",
            ):
                val = getattr(r, field, "") or ""
                if getattr(p, field) != val:
                    setattr(p, field, val)
                    changed = True
            if changed:
                p.save()

        FirmySearchHit.objects.get_or_create(
            run_id=r.run_id,
            position=r.position,
            defaults={"premise_id": p.id},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0003_add_website_url"),
    ]

    operations = [
        migrations.CreateModel(
            name="FirmyPremise",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("premise_id", models.PositiveIntegerField(db_index=True, unique=True, verbose_name="premise id")),
                ("title", models.CharField(max_length=500, verbose_name="название")),
                ("detail_url", models.URLField(max_length=2048, verbose_name="ссылка на профиль")),
                ("category", models.CharField(blank=True, max_length=300, verbose_name="категория")),
                ("address", models.CharField(blank=True, max_length=500, verbose_name="адрес")),
                ("card_text", models.TextField(blank=True, verbose_name="текст карточки (сырой)")),
                (
                    "phones",
                    models.TextField(
                        blank=True,
                        help_text="нормализованные номера, по одному на строку",
                        verbose_name="телефоны",
                    ),
                ),
                (
                    "emails",
                    models.TextField(
                        blank=True,
                        help_text="по одному на строку",
                        verbose_name="e-mail",
                    ),
                ),
                (
                    "website_url",
                    models.URLField(
                        blank=True,
                        help_text="ссылка с профиля (часто кнопка «Web» — может вести на соцсеть)",
                        max_length=2048,
                        verbose_name="сайт",
                    ),
                ),
                ("first_seen_at", models.DateTimeField(auto_now_add=True, verbose_name="впервые замечено")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="обновлено")),
            ],
            options={
                "verbose_name": "карточка Firmy",
                "verbose_name_plural": "карточки Firmy",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.CreateModel(
            name="FirmySearchHit",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField(db_index=True, verbose_name="позиция в выдаче")),
                (
                    "premise",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hits",
                        to="firmy.FirmyPremise",
                        verbose_name="карточка",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hits",
                        to="firmy.FirmySearchRun",
                        verbose_name="запуск",
                    ),
                ),
            ],
            options={
                "verbose_name": "попадание Firmy",
                "verbose_name_plural": "попадания Firmy",
                "ordering": ("run", "position"),
                "unique_together": {("run", "position"), ("run", "premise")},
            },
        ),
        migrations.RunPython(forwards_copy_legacy, migrations.RunPython.noop),
    ]

