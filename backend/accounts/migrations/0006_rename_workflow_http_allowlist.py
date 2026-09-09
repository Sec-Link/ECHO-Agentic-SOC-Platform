from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_systemsettings_workflow_http_private_allowlist"),
    ]

    operations = [
        migrations.RenameField(
            model_name="systemsettings",
            old_name="workflow_http_private_allowlist",
            new_name="workflow_http_allowlist",
        ),
    ]
