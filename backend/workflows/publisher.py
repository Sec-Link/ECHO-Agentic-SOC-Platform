"""Workflow publisher for versioned and atomic Prefect manifests."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import UUID

from .prefect.actions import ActionRegistry

from .models import Workflow, WorkflowStep
from .persistence import persist_workflow_definition
from .secret_config import (
    SecretConfigError,
    decrypt_sensitive_value,
    is_encrypted,
    prepare_config_for_storage,
    sensitive_fields,
)

logger = logging.getLogger(__name__)

GENERATED_FLOWS_DIR = Path(__file__).resolve().parent / 'flows' / 'generated'
CURRENT_POINTER_FILENAME = 'current.json'


def _serialize_step(step: WorkflowStep) -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    if step.action_template and step.action_template.default_config:
        config.update(step.action_template.default_config)
    config.update(step.action_config or {})
    return {
        'id': str(step.id),
        'order': step.order,
        'name': step.name,
        'node_type': step.node_type,
        'node_category': step.node_category,
        'action_type': step.action_type,
        'action_config': config,
        'timeout_seconds': step.timeout_seconds,
        'on_failure': step.on_failure,
        'retry_count': step.retry_count,
        'retry_delay_seconds': step.retry_delay_seconds,
        'condition': step.condition or {},
        'next_step_true': str(step.next_step_true) if step.next_step_true else None,
        'next_step_false': str(step.next_step_false) if step.next_step_false else None,
        'connections': list(step.connections or []),
        'is_active': step.is_active,
    }


def serialize_workflow(workflow: Workflow) -> Dict[str, Any]:
    steps = workflow.steps.filter(is_active=True).select_related('action_template').order_by('order')
    return {
        'id': str(workflow.id),
        'name': workflow.name,
        'description': workflow.description,
        'trigger_type': workflow.trigger_type,
        'edges': workflow.edges or [],
        'steps': [_serialize_step(step) for step in steps],
    }


def _workflow_dir(workflow: Workflow) -> Path:
    return GENERATED_FLOWS_DIR / str(workflow.id)


def _manifest_filename(version: int) -> str:
    return f'v{version}.json'


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.stem + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _atomic_create_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically create an immutable JSON file without replacing a peer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.stem + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
        os.link(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _active_steps(workflow: Workflow) -> List[Dict[str, Any]]:
    return list((serialize_workflow(workflow).get('steps') or []))


def _validate_action_types(steps: Iterable[Dict[str, Any]]) -> None:
    supported = set(ActionRegistry.get_all_actions())
    invalid: List[str] = []
    for step in steps:
        node_type = step.get('node_type')
        action_type = str(step.get('action_type') or '').strip()
        if node_type != 'action':
            continue
        if action_type not in supported:
            invalid.append(f"{step.get('name') or action_type}: {action_type}")
    if invalid:
        joined = '; '.join(invalid)
        raise ValueError(f'Unsupported workflow action types: {joined}')


def _validate_action_configs(steps: Iterable[Dict[str, Any]]) -> None:
    invalid: List[str] = []
    for step in steps:
        if step.get('node_type') != 'action':
            continue
        action_type = str(step.get('action_type') or '').strip()
        config = step.get('action_config') or {}
        try:
            # Validation only: reuse stored ciphertext without decrypting values
            # into the manifest or changing the database configuration.
            prepare_config_for_storage(
                action_type,
                {},
                existing=config,
                require_sensitive=True,
            )
        except SecretConfigError as exc:
            detail = '; '.join(str(message) for message in exc.messages)
            invalid.append(f"{step.get('name') or action_type}: {detail}")

    if invalid:
        raise ValueError(
            'Workflow action configuration is incomplete: ' + '; '.join(invalid)
        )


def _build_manifest_record(workflow: Workflow, version: int) -> Dict[str, Any]:
    payload = serialize_workflow(workflow)
    payload['trigger_conditions'] = workflow.trigger_conditions or {}
    payload['_meta'] = {
        'published_at': datetime.now(dt_timezone.utc).isoformat(),
        'workflow_db_id': str(workflow.id),
        'version': version,
        'execution_engine': 'prefect',
        'trigger_type': workflow.trigger_type,
        'trigger_conditions': workflow.trigger_conditions or {},
        'schedule_cron': workflow.schedule_cron or None,
        'tags': list(workflow.tags or []),
        'manifest_filename': _manifest_filename(version),
        'workflow_name': workflow.name,
    }
    return payload


def _current_pointer_payload(workflow: Workflow, version: int, published_at: str) -> Dict[str, Any]:
    return {
        'workflow_id': str(workflow.id),
        'workflow_name': workflow.name,
        'current_version': version,
        'manifest_filename': _manifest_filename(version),
        'published_at': published_at,
    }


def _manifest_path_for_version(workflow: Workflow, version: int) -> Path:
    return _workflow_dir(workflow) / _manifest_filename(version)


def _current_pointer_path(workflow: Workflow) -> Path:
    return _workflow_dir(workflow) / CURRENT_POINTER_FILENAME


def resolve_manifest_metadata(workflow: Workflow) -> Dict[str, Any]:
    pointer_path = _current_pointer_path(workflow)
    if not pointer_path.exists():
        raise FileNotFoundError(f'Published manifest pointer not found for workflow {workflow.id}')
    with open(pointer_path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def load_current_published_manifest(workflow: Workflow) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Load and validate the manifest selected by a workflow's current pointer."""
    pointer = resolve_manifest_metadata(workflow)
    manifest_filename = str(pointer.get('manifest_filename') or '').strip()
    try:
        manifest_version = int(pointer.get('current_version') or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError('Published manifest version is invalid.') from exc

    if manifest_version < 1 or not manifest_filename:
        raise ValueError('Published manifest pointer is incomplete.')
    if Path(manifest_filename).name != manifest_filename:
        raise ValueError('Published manifest filename is invalid.')
    if str(pointer.get('workflow_id') or '') != str(workflow.id):
        raise ValueError('Published manifest pointer belongs to another workflow.')

    manifest = load_published_manifest(workflow, manifest_version)
    if manifest_filename != _manifest_filename(manifest_version):
        raise ValueError('Published manifest filename does not match its version.')
    return pointer, manifest


def get_published_state(workflow: Workflow) -> Dict[str, Any]:
    try:
        pointer, _ = load_current_published_manifest(workflow)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return {
            'published_version': None,
            'published_at': None,
            'has_unpublished_changes': bool(workflow.is_draft),
        }

    return {
        'published_version': pointer.get('current_version'),
        'published_at': pointer.get('published_at'),
        'has_unpublished_changes': bool(workflow.is_draft),
    }


def build_published_export(workflow: Workflow) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Return the last published manifest, including encrypted secret values."""
    pointer, manifest = load_current_published_manifest(workflow)
    exported = deepcopy(manifest)
    meta = exported.setdefault('_meta', {})
    meta['export_source'] = 'last_published_manifest'
    meta['contains_encrypted_sensitive_values'] = True
    return exported, pointer


def _next_publish_version(workflow: Workflow) -> int:
    published_versions = [
        int(path.stem[1:])
        for path in _workflow_dir(workflow).glob('v*.json')
        if path.stem[1:].isdigit() and int(path.stem[1:]) > 0
    ]
    try:
        pointer = resolve_manifest_metadata(workflow)
        pointer_version = int(pointer.get('current_version') or 0)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        pointer_version = 0

    if published_versions or pointer_version > 0:
        return max([int(workflow.version or 1), pointer_version, *published_versions]) + 1
    return max(int(workflow.version or 1), 1)


def load_manifest_definition(workflow: Workflow, version: int) -> Dict[str, Any]:
    manifest_path = _manifest_path_for_version(workflow, version)
    if not manifest_path.exists():
        raise FileNotFoundError(f'Published manifest not found: {manifest_path.name}')
    with open(manifest_path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def _validate_published_manifest(
    workflow: Workflow,
    version: int,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate the identity and executable structure of one manifest."""
    if not isinstance(manifest, dict):
        raise ValueError('Published workflow manifest must be an object.')
    meta = manifest.get('_meta')
    if not isinstance(meta, dict):
        raise ValueError('Published workflow manifest metadata is invalid.')
    if str(manifest.get('id') or '') != str(workflow.id) or str(meta.get('workflow_db_id') or '') != str(workflow.id):
        raise ValueError('Published workflow manifest belongs to another workflow.')
    try:
        manifest_version = int(meta.get('version') or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError('Published workflow manifest version is invalid.') from exc
    if manifest_version != version:
        raise ValueError('Published workflow manifest version does not match the requested version.')
    if str(meta.get('manifest_filename') or '') != _manifest_filename(version):
        raise ValueError('Published workflow manifest filename does not match its version.')

    steps = manifest.get('steps')
    if not isinstance(steps, list):
        raise ValueError('Published workflow manifest steps must be a list.')
    step_ids = set()
    validated_steps = []
    node_types = {choice[0] for choice in WorkflowStep.NODE_TYPE_CHOICES}
    failure_modes = {choice[0] for choice in WorkflowStep.ON_FAILURE_CHOICES}
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Published workflow manifest contains an invalid step.')
        try:
            step_id = str(UUID(str(step.get('id') or '')))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('Published workflow manifest contains an invalid step ID.') from exc
        if step_id in step_ids:
            raise ValueError(f'Published workflow manifest contains duplicate step ID {step_id}.')
        step_ids.add(step_id)
        if not isinstance(step.get('name'), str):
            raise ValueError(f'Published workflow step {step_id} has an invalid name.')
        order = step.get('order')
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError(f'Published workflow step {step_id} has an invalid order.')
        node_type = step.get('node_type')
        if node_type not in node_types:
            raise ValueError(f'Published workflow step {step_id} has an invalid node type.')
        action_type = step.get('action_type')
        if not isinstance(action_type, str) or (node_type == 'action' and not action_type.strip()):
            raise ValueError(f'Published workflow step {step_id} has an invalid action type.')
        if not isinstance(step.get('node_category'), str):
            raise ValueError(f'Published workflow step {step_id} has an invalid node category.')
        if not isinstance(step.get('action_config'), dict):
            raise ValueError(f'Published workflow step {step_id} has an invalid action config.')
        if not isinstance(step.get('condition'), dict):
            raise ValueError(f'Published workflow step {step_id} has an invalid condition.')
        if not isinstance(step.get('connections'), list):
            raise ValueError(f'Published workflow step {step_id} has invalid connections.')
        if step.get('on_failure') not in failure_modes:
            raise ValueError(f'Published workflow step {step_id} has an invalid failure mode.')
        for field in ('timeout_seconds', 'retry_count', 'retry_delay_seconds'):
            value = step.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'Published workflow step {step_id} has an invalid {field}.')
        if not isinstance(step.get('is_active'), bool):
            raise ValueError(f'Published workflow step {step_id} has an invalid active flag.')
        validated_steps.append((step_id, step))

    for step_id, step in validated_steps:
        targets = list(step['connections'])
        targets.extend(
            step.get(field)
            for field in ('next_step_true', 'next_step_false')
            if step.get(field) is not None
        )
        for target in targets:
            try:
                target_id = str(UUID(str(target)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f'Published workflow step {step_id} has an invalid target step ID.') from exc
            if target_id not in step_ids:
                raise ValueError(
                    f'Published workflow step {step_id} references missing step {target_id}.'
                )
    return manifest


def load_published_manifest(workflow: Workflow, version: int) -> Dict[str, Any]:
    """Load one immutable workflow version and validate its execution identity."""
    try:
        version = int(version)
    except (TypeError, ValueError) as exc:
        raise ValueError('Published manifest version is invalid.') from exc
    if version < 1:
        raise ValueError('Published manifest version is invalid.')
    return _validate_published_manifest(
        workflow,
        version,
        load_manifest_definition(workflow, version),
    )


def load_manifest_definition_by_ref(manifest_ref: str) -> Dict[str, Any]:
    manifest_path = GENERATED_FLOWS_DIR / manifest_ref
    if not manifest_path.exists():
        raise FileNotFoundError(f'Published manifest not found: {manifest_ref}')
    with open(manifest_path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def publish_workflow(
    workflow: Workflow,
    *,
    register_deployment: bool = True,
) -> Dict[str, Any]:
    active_steps = _active_steps(workflow)
    _validate_action_types(active_steps)
    _validate_action_configs(active_steps)

    publish_version = _next_publish_version(workflow)
    manifest = _build_manifest_record(workflow, publish_version)
    _validate_published_manifest(workflow, publish_version, manifest)
    manifest_path = _manifest_path_for_version(workflow, publish_version)
    pointer_path = _current_pointer_path(workflow)
    published_at = manifest['_meta']['published_at']

    if manifest_path.exists():
        raise FileExistsError(f'Published manifest already exists: {manifest_path.name}')
    _atomic_create_json(manifest_path, manifest)
    _atomic_write_json(pointer_path, _current_pointer_payload(workflow, publish_version, published_at))

    workflow.version = publish_version
    workflow.execution_engine = 'prefect'
    workflow.is_draft = False
    workflow.save(update_fields=['version', 'execution_engine', 'is_draft', 'updated_at'])

    logger.info('Published workflow "%s" (id=%s) version %s to %s', workflow.name, workflow.id, publish_version, manifest_path)
    return {
        'slug': str(workflow.id),
        'manifest_ref': f'{workflow.id}/{manifest_path.name}',
        'manifest_path': str(manifest_path),
        'manifest_version': publish_version,
        'manifest_filename': manifest_path.name,
        'published_at': published_at,
        'steps_count': len(active_steps),
        'deployment_registered': False,
        'deployment_id': workflow.prefect_deployment_id or None,
    }


def list_published_manifests() -> List[Dict[str, Any]]:
    # Intentionally retained but not exposed by URLs/UI. The product does not
    # currently support disaster recovery from server-local manifests, and
    # restoring one can mismatch database UUIDs and published-state pointers.
    if not GENERATED_FLOWS_DIR.exists():
        return []

    manifests: List[Dict[str, Any]] = []
    for pointer_file in sorted(GENERATED_FLOWS_DIR.glob(f'*/{CURRENT_POINTER_FILENAME}')):
        try:
            with open(pointer_file, 'r', encoding='utf-8') as handle:
                pointer = json.load(handle)
            workflow_dir = pointer_file.parent
            manifest_filename = pointer.get('manifest_filename') or ''
            manifest_path = workflow_dir / manifest_filename
            with open(manifest_path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            meta = data.get('_meta', {})
            manifests.append({
                'filename': f"{workflow_dir.name}/{manifest_filename}",
                'slug': workflow_dir.name,
                'name': data.get('name', workflow_dir.name),
                'description': data.get('description', ''),
                'steps_count': len(data.get('steps', [])),
                'published_at': meta.get('published_at'),
                'version': meta.get('version'),
                'trigger_type': meta.get('trigger_type', 'manual'),
                'tags': meta.get('tags', []),
                'has_flow_file': False,
            })
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning('Skipping invalid manifest pointer %s: %s', pointer_file, exc)
    return manifests


def import_workflow_from_manifest(
    filename: str,
    *,
    created_by,
    update_existing: bool = True,
) -> Workflow:
    # Intentionally retained as dormant recovery code. See the comment on
    # list_published_manifests; normal transfers must use uploaded JSON.
    manifest_path = GENERATED_FLOWS_DIR / filename
    if not manifest_path.exists():
        raise FileNotFoundError(f'Manifest file not found: {filename}')

    with open(manifest_path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)
    return import_workflow_from_json_payload(
        payload,
        created_by=created_by,
        update_existing=update_existing,
    )


def import_workflow_from_json_payload(
    payload: Dict[str, Any],
    *,
    created_by,
    update_existing: bool = True,
    import_report: Dict[str, Any] | None = None,
) -> Workflow:
    payload = deepcopy(payload)
    removed_secret_fields: List[Dict[str, Any]] = []
    for step in payload.get('steps') or []:
        if not isinstance(step, dict):
            continue
        action_type = str(step.get('action_type') or '').strip()
        config = step.get('action_config')
        if not isinstance(config, dict):
            continue

        original_config = deepcopy(config)
        invalid_fields: List[str] = []
        for field in sensitive_fields(action_type):
            value = original_config.get(field)
            if not is_encrypted(value):
                continue
            try:
                decrypt_sensitive_value(
                    action_type,
                    field,
                    value,
                    original_config,
                )
            except SecretConfigError:
                invalid_fields.append(field)

        for field in invalid_fields:
            config.pop(field, None)
        if invalid_fields:
            removed_secret_fields.append({
                'step_name': str(step.get('name') or action_type or 'Unnamed step'),
                'action_type': action_type,
                'fields': sorted(invalid_fields),
            })

    if import_report is not None:
        import_report['removed_secret_fields'] = removed_secret_fields

    meta = payload.get('_meta', {})
    trigger_type = meta.get('trigger_type') or payload.get('trigger_type', 'manual')
    trigger_conditions = meta.get('trigger_conditions') or payload.get('trigger_conditions') or {}
    schedule_cron = meta.get('schedule_cron')
    tags = meta.get('tags') or payload.get('tags', [])

    workflow = persist_workflow_definition(
        workflow_definition=payload,
        created_by=created_by,
        trigger_type=trigger_type,
        trigger_conditions=trigger_conditions,
        schedule_cron=schedule_cron,
        is_active=False,
        is_draft=True,
        tags=tags,
        update_existing=update_existing,
        require_sensitive=False,
        preserve_existing_secrets=False,
    )
    logger.info('Imported workflow "%s" (id=%s) as an inactive draft', workflow.name, workflow.id)
    return workflow
