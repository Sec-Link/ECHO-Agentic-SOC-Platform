from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_systemsettings_auto_approve_default_true"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="workflow_http_private_allowlist",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
