# Generated manually for MCP/Skill monitor tables
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("ai_assistant", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPToolExecution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tool_name", models.CharField(db_index=True, max_length=200)),
                ("arguments", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(db_index=True, max_length=32)),
                ("error", models.TextField(blank=True)),
                ("start_time", models.DateTimeField(auto_now_add=True)),
                ("end_time", models.DateTimeField(blank=True, null=True)),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("endpoint", models.CharField(blank=True, max_length=500)),
                ("source", models.CharField(blank=True, max_length=32)),
            ],
            options={
                "db_table": "ai_mcp_tool_executions",
                "indexes": [
                    models.Index(fields=["tool_name"], name="ai_mcp_tool_idx"),
                    models.Index(fields=["status"], name="ai_mcp_status_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="MCPToolStats",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tool_name", models.CharField(max_length=200, unique=True)),
                ("total_calls", models.IntegerField(default=0)),
                ("success_calls", models.IntegerField(default=0)),
                ("failed_calls", models.IntegerField(default=0)),
                ("last_call_time", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "ai_mcp_tool_stats",
            },
        ),
        migrations.CreateModel(
            name="SkillStats",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("skill_name", models.CharField(max_length=200, unique=True)),
                ("total_calls", models.IntegerField(default=0)),
                ("success_calls", models.IntegerField(default=0)),
                ("failed_calls", models.IntegerField(default=0)),
                ("last_call_time", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "ai_skill_stats",
            },
        ),
    ]
