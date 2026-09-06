"""Publish the system-managed API credential through Prefect's Secret store."""

import os
import logging

from prefect.blocks.system import Secret

from . import prefect_client
from .models import Workflow
from .worker_auth import ensure_worker_credential

logger = logging.getLogger(__name__)


def sync_worker_credential():
    if not prefect_client.has_api():
        raise prefect_client.PrefectConfigError('PREFECT_API_URL is required to provision worker access.')
    try:
        credential = ensure_worker_credential()
        Secret(value=credential.key).save(credential.block_name, overwrite=True)
    except Exception as exc:
        # SDK errors can include response bodies; never expose secret values.
        raise prefect_client.PrefectAPIError(
            f'Could not provision the workflow worker Secret ({type(exc).__name__}).'
        ) from None
    return credential.block_name


def bootstrap_worker_credentials():
    block_name = sync_worker_credential()
    # Deployment defaults also cover schedules published before automatic auth.
    # Only a Secret reference is stored in parameters; workers load it per run.
    deployment_ids = set(Workflow.objects.filter(execution_engine='prefect', is_active=True).exclude(
        prefect_deployment_id='',
    ).values_list('prefect_deployment_id', flat=True))
    deployment_ids.add(os.getenv('PREFECT_DEPLOYMENT_ID', '').strip())
    for deployment_id in sorted(value for value in deployment_ids if value):
        try:
            deployment = prefect_client.get_deployment(deployment_id)
        except prefect_client.PrefectDeploymentNotFound:
            logger.warning('Skipped missing Prefect deployment %s while configuring worker access.', deployment_id)
            continue
        parameters = dict(deployment.get('parameters') or {})
        if parameters.get('worker_credential_block') != block_name:
            parameters['worker_credential_block'] = block_name
            prefect_client.update_deployment(deployment_id=deployment_id, payload={'parameters': parameters})
    return block_name
