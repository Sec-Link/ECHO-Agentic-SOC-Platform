# Generated migration to add dashboard time fields
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('dashboards', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_field',
            field=models.CharField(max_length=200, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='time_selector',
            field=models.CharField(max_length=50, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_relative',
            field=models.CharField(max_length=50, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_relative_custom_value',
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_relative_custom_unit',
            field=models.CharField(max_length=10, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_from',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='dashboard',
            name='timestamp_to',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
