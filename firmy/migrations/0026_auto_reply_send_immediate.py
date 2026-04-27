from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("firmy", "0025_agent_scope_and_strategy_prompts"),
    ]

    operations = [
        migrations.AddField(
            model_name="firmyprocessingitem",
            name="auto_reply_send_immediate",
            field=models.BooleanField(
                default=False,
                help_text="Если да — при входящих и фоновом автоответе текст сразу в переписке; если нет — черновик с подтверждением.",
                verbose_name="автоответ сразу в чат (без черновика)",
            ),
        ),
    ]
