from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_assistant", "0002_mcp_skill_monitor"),
    ]

    operations = [
        migrations.CreateModel(
            name="SkillConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200, unique=True)),
                ("version", models.CharField(default="v1", max_length=50)),
                ("route", models.CharField(blank=True, max_length=200)),
                ("enabled", models.BooleanField(default=True)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "ai_skill_configs",
            },
        ),
    ]
