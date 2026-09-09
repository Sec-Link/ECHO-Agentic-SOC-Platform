from django.db import migrations, models
from django.db.models import Count
import django.db.models.deletion


def backfill_execution_snapshots(apps, schema_editor):
    Workflow = apps.get_model('workflows', 'Workflow')
    WorkflowExecution = apps.get_model('workflows', 'WorkflowExecution')
    StepExecution = apps.get_model('workflows', 'StepExecution')

    workflow_versions = dict(Workflow.objects.values_list('id', 'version'))
    for execution in WorkflowExecution.objects.all().iterator():
        context = execution.context if isinstance(execution.context, dict) else {}
        version = 0
        for key in ('workflow_version', 'manifest_version'):
            try:
                version = int(context.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if version >= 1:
                break
        if version < 1:
            version = max(int(workflow_versions.get(execution.workflow_id) or 1), 1)
        WorkflowExecution.objects.filter(pk=execution.pk).update(workflow_version=version)

    for step_execution in StepExecution.objects.select_related('step').all().iterator():
        step = step_execution.step
        StepExecution.objects.filter(pk=step_execution.pk).update(
            source_step_id=step.id,
            step_name=step.name,
            step_order=step.order,
            action_type=step.action_type,
        )

    # The retired local executor could store one row per retry. Never discard
    # those audit rows implicitly; an operator must archive duplicates first.
    duplicates = (
        StepExecution.objects.values('workflow_execution_id', 'source_step_id')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            'Legacy StepExecution retry rows must be archived before applying '
            'the Prefect source-step uniqueness constraint.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('workflows', '0007_unique_prefect_flow_run'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflow',
            name='execution_engine',
            field=models.CharField(
                choices=[('prefect', 'Prefect')],
                default='prefect',
                help_text='Engine that runs this workflow. New workflows use Prefect.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='workflowexecution',
            name='workflow_version',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='stepexecution',
            name='source_step_id',
            field=models.UUIDField(null=True),
        ),
        migrations.AddField(
            model_name='stepexecution',
            name='step_name',
            field=models.CharField(max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='stepexecution',
            name='step_order',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name='stepexecution',
            name='action_type',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='stepexecution',
            name='step',
            field=models.ForeignKey(
                blank=True,
                help_text='Current draft step when it still exists',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='executions',
                to='workflows.workflowstep',
            ),
        ),
        migrations.RunPython(backfill_execution_snapshots, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='workflowexecution',
            name='workflow_version',
            field=models.PositiveIntegerField(
                help_text='Immutable published workflow version used by this execution',
            ),
        ),
        migrations.AlterField(
            model_name='stepexecution',
            name='source_step_id',
            field=models.UUIDField(
                help_text='Immutable step ID from the published workflow manifest',
            ),
        ),
        migrations.AlterField(
            model_name='stepexecution',
            name='step_name',
            field=models.CharField(
                help_text='Immutable step name from the published workflow manifest',
                max_length=200,
            ),
        ),
        migrations.AlterField(
            model_name='stepexecution',
            name='step_order',
            field=models.PositiveIntegerField(
                help_text='Immutable step order from the published workflow manifest',
            ),
        ),
        migrations.AlterField(
            model_name='stepexecution',
            name='action_type',
            field=models.CharField(
                blank=True,
                help_text='Immutable action type from the published workflow manifest',
                max_length=100,
            ),
        ),
        migrations.AlterModelOptions(
            name='stepexecution',
            options={
                'ordering': ['workflow_execution', 'step_order'],
                'verbose_name': 'Step Execution',
                'verbose_name_plural': 'Step Executions',
            },
        ),
        migrations.AddConstraint(
            model_name='stepexecution',
            constraint=models.UniqueConstraint(
                fields=('workflow_execution', 'source_step_id'),
                name='uniq_step_exec_source',
            ),
        ),
    ]
