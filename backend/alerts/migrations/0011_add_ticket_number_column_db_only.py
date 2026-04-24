from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0010_alert_ticket_number_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE alerts_alert "
                        "ADD COLUMN IF NOT EXISTS ticket_number varchar(64) NULL;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
