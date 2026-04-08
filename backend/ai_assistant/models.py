import uuid
from django.db import models
from tickets.models import EventTicket


class ExternalMCPServer(models.Model):
    name = models.CharField(max_length=120, unique=True)
    endpoint = models.URLField(max_length=500)
    title = models.CharField(max_length=200, blank=True)
    token = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_external_mcp_servers"

    def __str__(self) -> str:
        return f"{self.name} ({self.endpoint})"


class SkillConfig(models.Model):
    name = models.CharField(max_length=200, unique=True)
    version = models.CharField(max_length=50, default="v1")
    route = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_skill_configs"

    def __str__(self) -> str:
        return self.name


class KnowledgeBaseItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.CharField(max_length=200, db_index=True)
    title = models.CharField(max_length=300)
    file_path = models.CharField(max_length=600, unique=True)
    content = models.TextField()
    content_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_knowledge_base_items"
        indexes = [
            models.Index(fields=["category"], name="ai_kb_category_idx"),
        ]


class KnowledgeEmbedding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(KnowledgeBaseItem, on_delete=models.CASCADE, related_name="embeddings")
    chunk_index = models.IntegerField()
    chunk_text = models.TextField()
    embedding = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_knowledge_embeddings"
        indexes = [
            models.Index(fields=["item"], name="ai_kb_item_idx"),
        ]


class KnowledgeRetrievalLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation_id = models.CharField(max_length=128, blank=True)
    message_id = models.CharField(max_length=128, blank=True)
    query = models.TextField()
    risk_type = models.CharField(max_length=200, blank=True)
    retrieved_item_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_knowledge_retrieval_logs"


class MCPToolExecution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_name = models.CharField(max_length=200, db_index=True)
    arguments = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, db_index=True)
    error = models.TextField(blank=True)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    endpoint = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=32, blank=True)

    class Meta:
        db_table = "ai_mcp_tool_executions"
        indexes = [
            models.Index(fields=["tool_name"], name="ai_mcp_tool_idx"),
            models.Index(fields=["status"], name="ai_mcp_status_idx"),
        ]


class MCPToolStats(models.Model):
    tool_name = models.CharField(max_length=200, unique=True)
    total_calls = models.IntegerField(default=0)
    success_calls = models.IntegerField(default=0)
    failed_calls = models.IntegerField(default=0)
    last_call_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_mcp_tool_stats"


class SkillStats(models.Model):
    skill_name = models.CharField(max_length=200, unique=True)
    total_calls = models.IntegerField(default=0)
    success_calls = models.IntegerField(default=0)
    failed_calls = models.IntegerField(default=0)
    last_call_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_skill_stats"


class TicketAIChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(EventTicket, on_delete=models.CASCADE, to_field="ticket_number", db_column="ticket_number")
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=32)
    content = models.TextField()
    trace = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_ticket_chat_messages"
        indexes = [
            models.Index(fields=["ticket"], name="ai_ticket_chat_idx"),
            models.Index(fields=["created_at"], name="ai_ticket_chat_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.ticket_id} {self.role} @ {self.created_at}"
