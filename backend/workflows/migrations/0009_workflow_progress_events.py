from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workflows', '0008_prefect_execution_snapshots')]

    operations = [
        migrations.AddField(
            model_name='workflowexecution',
            name='runtime_event_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workflowexecution',
            name='state_event_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='WorkflowEventCheckpoint',
            fields=[
                ('name', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('occurred', models.DateTimeField()),
            ],
        ),
    ]
