"""Encrypt or rotate sensitive workflow action configuration."""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from workflows.models import ActionTemplate, SavedWorkflowNode, StepExecution, Workflow, WorkflowStep
from workflows.publisher import GENERATED_FLOWS_DIR, _atomic_write_json, publish_workflow
from workflows.secret_config import (
    SecretConfigError,
    prepare_config_for_storage,
    rotate_config,
)


class Command(BaseCommand):
    help = (
        "Encrypt plaintext workflow secrets in database rows and Prefect manifests. "
        "The command is a dry-run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the validated changes. Without this flag no writes occur.",
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Re-encrypt existing ciphertext with the first WORKFLOW_ENCRYPTION_KEYS key.",
        )
        parser.add_argument(
            "--skip-publish",
            action="store_true",
            help="Do not publish a fresh version of currently published Prefect workflows.",
        )

    @staticmethod
    def _secure(action_type, config, rotate):
        secured = prepare_config_for_storage(action_type or "", config or {})
        return rotate_config(action_type or "", secured) if rotate else secured

    def _collect_database_changes(self, rotate):
        changes = []
        for item in ActionTemplate.objects.all().iterator():
            secured = self._secure(item.action_type, item.default_config, rotate)
            if secured != (item.default_config or {}):
                changes.append((ActionTemplate, item.pk, "default_config", secured))

        for item in WorkflowStep.objects.all().iterator():
            secured = self._secure(item.action_type, item.action_config, rotate)
            if secured != (item.action_config or {}):
                changes.append((WorkflowStep, item.pk, "action_config", secured))

        for item in SavedWorkflowNode.objects.all().iterator():
            secured = self._secure(item.action_type, item.action_config, rotate)
            if secured != (item.action_config or {}):
                changes.append((SavedWorkflowNode, item.pk, "action_config", secured))

        for item in StepExecution.objects.all().iterator():
            current = item.input_data or {}
            if not isinstance(current, dict):
                continue
            if isinstance(current.get("action_config"), dict):
                secured = dict(current)
                secured["action_config"] = self._secure(
                    item.action_type, current["action_config"], rotate
                )
            else:
                secured = self._secure(item.action_type, current, rotate)
            if secured != current:
                changes.append((StepExecution, item.pk, "input_data", secured))
        return changes

    def _collect_manifest_changes(self, rotate):
        changes = []
        if not GENERATED_FLOWS_DIR.exists():
            return changes
        for path in sorted(GENERATED_FLOWS_DIR.glob("*/v*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise CommandError(f"Cannot read workflow manifest {path}: {exc}") from exc

            secured_payload = dict(payload)
            secured_steps = []
            changed = False
            for step in payload.get("steps") or []:
                secured_step = dict(step)
                config = step.get("action_config") or {}
                secured_config = self._secure(step.get("action_type") or "", config, rotate)
                secured_step["action_config"] = secured_config
                secured_steps.append(secured_step)
                changed = changed or secured_config != config
            secured_payload["steps"] = secured_steps
            if changed:
                changes.append((path, secured_payload))
        return changes

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        rotate = bool(options["rotate"])
        try:
            database_changes = self._collect_database_changes(rotate)
            manifest_changes = self._collect_manifest_changes(rotate)
        except (SecretConfigError, ImproperlyConfigured, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        published_workflows = list(
            Workflow.objects.filter(execution_engine="prefect", is_draft=False)
        ) if (database_changes or manifest_changes) else []
        self.stdout.write(
            f"Database rows to rewrite: {len(database_changes)}; "
            f"manifest files to rewrite: {len(manifest_changes)}; "
            f"workflows to republish: {0 if options['skip_publish'] else len(published_workflows)}"
        )

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run only; no data was changed."))
            return

        try:
            with transaction.atomic():
                for model, pk, field, value in database_changes:
                    model.objects.filter(pk=pk).update(**{field: value})
                for path, payload in manifest_changes:
                    _atomic_write_json(Path(path), payload)
        except OSError as exc:
            raise CommandError(f"Failed to atomically rewrite a workflow manifest: {exc}") from exc

        republished = 0
        if not options["skip_publish"]:
            for workflow in published_workflows:
                publish_workflow(workflow, register_deployment=False)
                republished += 1

        mode = "rotated" if rotate else "encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"Workflow secrets {mode}: {len(database_changes)} database rows, "
                f"{len(manifest_changes)} manifests; republished {republished} workflows."
            )
        )
