# Generated manually for ai_assistant models
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ExternalMCPServer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("endpoint", models.URLField(max_length=500)),
                ("title", models.CharField(blank=True, max_length=200)),
                ("token", models.TextField(blank=True)),
                ("enabled", models.BooleanField(default=True)),
                ("extra", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "ai_external_mcp_servers",
            },
        ),
        migrations.CreateModel(
            name="KnowledgeBaseItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(db_index=True, max_length=200)),
                ("title", models.CharField(max_length=300)),
                ("file_path", models.CharField(max_length=600, unique=True)),
                ("content", models.TextField()),
                ("content_hash", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "ai_knowledge_base_items",
                "indexes": [models.Index(fields=["category"], name="ai_kb_category_idx")],
            },
        ),
        migrations.CreateModel(
            name="KnowledgeEmbedding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("chunk_index", models.IntegerField()),
                ("chunk_text", models.TextField()),
                ("embedding", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "item",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="embeddings", to="ai_assistant.knowledgebaseitem"),
                ),
            ],
            options={
                "db_table": "ai_knowledge_embeddings",
                "indexes": [models.Index(fields=["item"], name="ai_kb_item_idx")],
            },
        ),
        migrations.CreateModel(
            name="KnowledgeRetrievalLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("conversation_id", models.CharField(blank=True, max_length=128)),
                ("message_id", models.CharField(blank=True, max_length=128)),
                ("query", models.TextField()),
                ("risk_type", models.CharField(blank=True, max_length=200)),
                ("retrieved_item_ids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "ai_knowledge_retrieval_logs",
            },
        ),
    ]
