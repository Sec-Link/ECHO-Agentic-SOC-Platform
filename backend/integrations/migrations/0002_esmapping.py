"""Manual migration: add ESMapping model

Note: Normally created by `makemigrations`. This manual migration mirrors the model added.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ESMapping',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('index', models.CharField(max_length=256, db_index=True)),
                ('table', models.CharField(max_length=256, db_index=True)),
                ('columns', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'unique_together': {('index', 'table')},
            },
        ),
    ]
