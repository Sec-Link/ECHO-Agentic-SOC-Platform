from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('workflows', '0006_simplify_ticket_workflow_binding_columns'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='workflowexecution',
            constraint=models.UniqueConstraint(
                condition=~models.Q(task_result_id=''),
                fields=('task_result_id',),
                name='uniq_workflow_execution_flow_run',
            ),
        ),
    ]
