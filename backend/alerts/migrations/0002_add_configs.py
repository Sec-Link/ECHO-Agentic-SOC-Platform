# Generated migration to add ESIntegrationConfig and WebhookConfig
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ESIntegrationConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.CharField(max_length=64, unique=True, db_index=True)),
                ('enabled', models.BooleanField(default=False)),
                ('hosts', models.TextField(blank=True, help_text='Comma separated hosts, e.g. http://es1:9200,http://es2:9200')),
                ('index', models.CharField(default='alerts', max_length=128)),
                ('username', models.CharField(blank=True, max_length=128)),
                ('password', models.CharField(blank=True, max_length=128)),
                ('use_ssl', models.BooleanField(default=False)),
                ('verify_certs', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='WebhookConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tenant_id', models.CharField(max_length=64, unique=True, db_index=True)),
                ('url', models.CharField(max_length=1024)),
                ('method', models.CharField(default='POST', max_length=8)),
                ('headers', models.JSONField(default=dict, blank=True)),
                ('active', models.BooleanField(default=True)),
            ],
        ),
    ]

