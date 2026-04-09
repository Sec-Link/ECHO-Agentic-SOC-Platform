from rest_framework import serializers

from .models import InterfaceEndpoint, InterfaceRequestLog


class InterfaceRequestLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterfaceRequestLog
        fields = [
            'id',
            'method',
            'source_ip',
            'response_status',
            'request_body',
            'response_body',
            'created_at',
        ]
        read_only_fields = fields


class InterfaceEndpointSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    ingest_url = serializers.SerializerMethodField()
    code_examples = serializers.SerializerMethodField()

    class Meta:
        model = InterfaceEndpoint
        fields = [
            'id',
            'name',
            'description',
            'interface_type',
            'secret_token',
            'hmac_secret',
            'is_active',
            'created_by',
            'created_by_username',
            'created_at',
            'updated_at',
            'last_event_at',
            'ingest_url',
            'code_examples',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_username', 'created_at', 'updated_at', 'last_event_at', 'ingest_url', 'code_examples']

    def get_ingest_url(self, obj):
        request = self.context.get('request')
        relative = f"/api/v1/interfaces/endpoints/{obj.id}/ingest/"
        if request:
            return request.build_absolute_uri(relative)
        return relative

    def get_code_examples(self, obj):
        ingest_url = self.get_ingest_url(obj)
        token = obj.secret_token
        payload = '{"event":"demo","ticket_number":"SEC-1001"}'
        return {
            'curl': (
                f"curl -X POST '{ingest_url}' "
                f"-H 'Content-Type: application/json' "
                f"-H 'X-Interface-Token: {token}' "
                f"-d '{payload}'"
            ),
            'python': (
                "import requests\n\n"
                f"url = '{ingest_url}'\n"
                f"headers = {{'X-Interface-Token': '{token}'}}\n"
                f"payload = {payload}\n"
                "resp = requests.post(url, json=payload, headers=headers, timeout=10)\n"
                "print(resp.status_code, resp.json())"
            ),
            'javascript': (
                f"fetch('{ingest_url}', {{\n"
                "  method: 'POST',\n"
                "  headers: {\n"
                "    'Content-Type': 'application/json',\n"
                f"    'X-Interface-Token': '{token}'\n"
                "  },\n"
                f"  body: JSON.stringify({payload})\n"
                "}).then(r => r.json()).then(console.log);"
            ),
        }

