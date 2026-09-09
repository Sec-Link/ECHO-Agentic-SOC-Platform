from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import workflows.models


class Migration(migrations.Migration):
    dependencies = [
        ('workflows', '0009_workflow_progress_events'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkflowWorkerCredential',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('key', models.CharField(default=workflows.models._worker_credential_key, editable=False, max_length=64)),
                ('block_name', models.CharField(default=workflows.models._worker_secret_block_name, editable=False, max_length=64, unique=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='workflow_worker_credential', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='workflow_worker_credential_singleton')],
            },
        ),
    ]
