from rest_framework import serializers
from ai_assistant.models import ExternalMCPServer, SkillConfig


class AIAssistantRequestSerializer(serializers.Serializer):
    alert_json = serializers.JSONField(required=False)
    trigger_rule = serializers.CharField(required=False, allow_blank=True)
    related_logs = serializers.JSONField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True)
    api_key = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    base_url = serializers.CharField(required=False, allow_blank=True)
    timeout_seconds = serializers.IntegerField(required=False)
    enabled = serializers.BooleanField(required=False)
    mcp_enabled = serializers.BooleanField(required=False)
    mcp_base_url = serializers.CharField(required=False, allow_blank=True)
    mcp_servers = serializers.JSONField(required=False)
    mcp_token = serializers.CharField(required=False, allow_blank=True)
    mcp_timeout_seconds = serializers.IntegerField(required=False)
    mcp_ticket_context_path = serializers.CharField(required=False, allow_blank=True)
    mcp_ticket_search_path = serializers.CharField(required=False, allow_blank=True)
    mcp_cmdb_lookup_path = serializers.CharField(required=False, allow_blank=True)
    mcp_observables_extract_path = serializers.CharField(required=False, allow_blank=True)
    skills = serializers.JSONField(required=False)

    def validate_related_logs(self, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    def validate_skills(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            if isinstance(item, dict):
                cleaned.append(item)
        return cleaned

    def validate_mcp_servers(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint") or "").strip()
            if not endpoint:
                continue
            cleaned.append(
                {
                    "endpoint": endpoint,
                    "title": str(item.get("title") or "").strip(),
                    "token": str(item.get("token") or "").strip(),
                }
            )
        return cleaned


class AIChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(required=True, allow_blank=False)
    messages = serializers.JSONField(required=False)
    ticket_number = serializers.CharField(required=False, allow_blank=True)
    api_key = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    base_url = serializers.CharField(required=False, allow_blank=True)
    timeout_seconds = serializers.IntegerField(required=False)
    max_iterations = serializers.IntegerField(required=False)
    skills = serializers.JSONField(required=False)
    mcp_enabled = serializers.BooleanField(required=False)
    mcp_base_url = serializers.CharField(required=False, allow_blank=True)
    mcp_servers = serializers.JSONField(required=False)
    mcp_token = serializers.CharField(required=False, allow_blank=True)
    mcp_timeout_seconds = serializers.IntegerField(required=False)

    def validate_messages(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = item.get("content")
            if role and content is not None:
                cleaned.append({"role": role, "content": content})
        return cleaned


class ExternalMCPServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalMCPServer
        fields = [
            "id",
            "name",
            "endpoint",
            "title",
            "token",
            "enabled",
            "extra",
            "created_at",
            "updated_at",
        ]

    def validate_skills(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            name = str(item.get("name") or item or "").strip() if isinstance(item, dict) else str(item).strip()
            if name:
                cleaned.append(name)
        return cleaned

    def validate_mcp_servers(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint") or "").strip()
            if not endpoint:
                continue
            cleaned.append(
                {
                    "endpoint": endpoint,
                    "title": str(item.get("title") or "").strip(),
                    "token": str(item.get("token") or "").strip(),
                }
            )
        return cleaned


class SkillConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SkillConfig
        fields = [
            "id",
            "name",
            "version",
            "route",
            "enabled",
            "description",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        if not validated_data.get("route"):
            validated_data["route"] = validated_data.get("name", "")
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "route" in validated_data and not validated_data.get("route"):
            validated_data["route"] = instance.name
        return super().update(instance, validated_data)
