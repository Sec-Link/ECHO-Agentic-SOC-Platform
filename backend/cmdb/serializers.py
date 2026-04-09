from rest_framework import serializers
from .models import Asset, AssetColumn, AssetAuditLog


class AssetColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetColumn
        fields = '__all__'


class AssetSerializer(serializers.ModelSerializer):
    created_user = serializers.CharField(source='created_by.username', read_only=True, default=None)
    updated_user = serializers.CharField(source='updated_by.username', read_only=True, default=None)

    class Meta:
        model = Asset
        fields = '__all__'
        read_only_fields = ['asset_number', 'created_by', 'updated_by', 'created_at', 'updated_at']


class AssetAuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True, default=None)

    class Meta:
        model = AssetAuditLog
        fields = '__all__'

