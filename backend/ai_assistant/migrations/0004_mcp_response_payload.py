from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_assistant", "0003_skill_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcptoolexecution",
            name="response_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
