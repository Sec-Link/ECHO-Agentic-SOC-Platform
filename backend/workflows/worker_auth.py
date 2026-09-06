"""Restricted service authentication for the Prefect worker's Django API calls."""

import secrets

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.urls import Resolver404, resolve
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission

from .models import WorkflowWorkerCredential


WORKER_USERNAME = 'argus-prefect-worker'
WORKER_AUTH_SCHEME = b'workflowworker'
_ALLOWED_ROUTES = {
    'workflow-worker-ticket-list': {'GET', 'POST'},
    'workflow-worker-ticket-detail': {'GET', 'PATCH'},
    'execution-runtime-policy': {'GET'},
}


def ensure_worker_credential():
    """Create once, preserving the secret and refusing to take over an existing user."""
    try:
        with transaction.atomic():
            credential = WorkflowWorkerCredential.objects.select_related('user').filter(pk=1).first()
            if credential is None:
                user = get_user_model().objects.create_user(username=WORKER_USERNAME, password=None)
                credential = WorkflowWorkerCredential.objects.create(pk=1, user=user)
    except IntegrityError:
        # Another initializer may have committed the singleton while we waited.
        credential = WorkflowWorkerCredential.objects.select_related('user').filter(pk=1).first()
        if credential is None:
            raise ImproperlyConfigured(
                f'Cannot initialize workflow worker: username {WORKER_USERNAME!r} is already in use.'
            ) from None
    if not credential.user.is_active:
        raise ImproperlyConfigured('The workflow worker service identity is disabled.')
    return credential


def _is_worker_header(request):
    header = get_authorization_header(request).split()
    return bool(header and header[0].lower() == WORKER_AUTH_SCHEME)


def _is_allowed_request(request):
    try:
        match = request.resolver_match or resolve(request.path_info)
    except Resolver404:
        return False
    return request.method in _ALLOWED_ROUTES.get(match.view_name, set())


class WorkflowWorkerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        if not _is_worker_header(request):
            return None
        if not _is_allowed_request(request):
            raise PermissionDenied('Workflow worker credential cannot access this endpoint.')
        parts = get_authorization_header(request).split()
        if len(parts) != 2:
            raise AuthenticationFailed('Invalid workflow worker credential.')
        credential = WorkflowWorkerCredential.objects.select_related('user').filter(pk=1).first()
        if credential is None or not secrets.compare_digest(parts[1], credential.key.encode('ascii')):
            raise AuthenticationFailed('Invalid workflow worker credential.')
        if not credential.user.is_active:
            raise AuthenticationFailed('Workflow worker service identity is disabled.')
        return credential.user, credential

    def authenticate_header(self, request):
        return 'WorkflowWorker'


class IsWorkflowWorker(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and isinstance(request.auth, WorkflowWorkerCredential))


class IsWorkflowWorkerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and (isinstance(request.auth, WorkflowWorkerCredential) or request.user.is_staff)
        )
