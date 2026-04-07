from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0005_dashboardstats_schedulerconfig'),
    ]

    operations = [
        # 瀵瑰簲 ALTER TABLE alerts_alert ADD COLUMN rule_id ...
        migrations.AddField(
            model_name='alert',
            name='rule_id',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN title ...
        migrations.AddField(
            model_name='alert',
            name='title',
            field=models.CharField(blank=True, max_length=256, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN status ...
        migrations.AddField(
            model_name='alert',
            name='status',
            field=models.IntegerField(blank=True, default=0, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN description ...
        migrations.AddField(
            model_name='alert',
            name='description',
            field=models.TextField(blank=True, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN category ...
        migrations.AddField(
            model_name='alert',
            name='category',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN source_data ...
        migrations.AddField(
            model_name='alert',
            name='source_data',
            field=models.JSONField(blank=True, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN created_at ...
        migrations.AddField(
            model_name='alert',
            name='created_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN updated_at ...
        migrations.AddField(
            model_name='alert',
            name='updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # 瀵瑰簲 ADD COLUMN deleted_at ...
        migrations.AddField(
            model_name='alert',
            name='deleted_at',
            field=models.IntegerField(blank=True, default=0, null=True),
        ),
    ]
