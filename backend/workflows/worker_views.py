"""Narrow worker-only ticket endpoints, reusing the existing ticket operations."""

from rest_framework import serializers

from tickets.serializers import EventTicketSerializer
from tickets.views import EventTicketViewSet

from .worker_auth import IsWorkflowWorker, WorkflowWorkerAuthentication


class WorkerTicketSerializer(EventTicketSerializer):
    def validate_is_deleted(self, value):
        raise serializers.ValidationError('Workflow workers cannot delete or restore tickets.')


class WorkerTicketViewSet(EventTicketViewSet):
    authentication_classes = [WorkflowWorkerAuthentication]
    permission_classes = [IsWorkflowWorker]

    def get_serializer_class(self):
        return super().get_serializer_class() if self.action == 'list' else WorkerTicketSerializer
