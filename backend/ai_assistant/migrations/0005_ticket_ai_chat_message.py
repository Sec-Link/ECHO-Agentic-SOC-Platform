from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0004_eventticket_labels"),
        ("ai_assistant", "0004_mcp_response_payload"),
    ]

    operations = [
        migrations.CreateModel(
            name="TicketAIChatMessage",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("role", models.CharField(max_length=32)),
                ("content", models.TextField()),
                ("trace", models.JSONField(default=list, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="auth.user")),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="tickets.eventticket", to_field="ticket_number", db_column="ticket_number")),
            ],
            options={
                "db_table": "ai_ticket_chat_messages",
            },
        ),
        migrations.AddIndex(
            model_name="ticketaichatmessage",
            index=models.Index(fields=["ticket"], name="ai_ticket_chat_idx"),
        ),
        migrations.AddIndex(
            model_name="ticketaichatmessage",
            index=models.Index(fields=["created_at"], name="ai_ticket_chat_time_idx"),
        ),
    ]
