from __future__ import annotations

from typing import Any, Dict, List

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run

from .actions import ActionRegistry
from .client import BackendAPIError, get_runtime_policy, register_runtime, sync_runtime
from .conditions import evaluate_condition_object, resolve_context_path
from .executor import execute_action


@task(name="execute-action")
def execute_action_task(action_type: str, action_config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    return execute_action(action_type, action_config, context)


def _validated_run(run: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if not isinstance(run, dict) or run.get("schema_version") != 1:
        raise ValueError("run.schema_version must be 1")
    workflow = run.get("workflow")
    trigger = run.get("trigger") or {}
    if not isinstance(workflow, dict) or not isinstance(workflow.get("definition"), dict):
        raise ValueError("run.workflow.definition must be an object")
    definition = workflow["definition"]
    if str(workflow.get("id") or "") != str(definition.get("id") or ""):
        raise ValueError("run workflow id does not match definition id")
    steps = definition.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError("workflow definition steps must be a list")
    ids = [str(step.get("id")) for step in steps if isinstance(step, dict) and step.get("id")]
    if len(ids) != len(set(ids)):
        raise ValueError("workflow definition contains duplicate step ids")
    if not isinstance(trigger, dict) or not isinstance(trigger.get("data") or {}, dict):
        raise ValueError("run.trigger must contain object data")
    return workflow, definition, trigger


def _next_step(ordered: List[Dict[str, Any]], positions: Dict[str, int], step: Dict[str, Any], condition: bool | None = None) -> str | None:
    if step.get("node_type") == "condition":
        target = step.get("next_step_true") if condition else step.get("next_step_false")
        if target:
            return str(target)
    if step.get("connections"):
        return str(step["connections"][0])
    index = positions.get(str(step.get("id")))
    return str(ordered[index + 1].get("id")) if index is not None and index + 1 < len(ordered) else None


def _snapshot(execution_id: str, prefect_id: str, status: str, current_step: int, total_steps: int, context: Dict[str, Any], results: List[Dict[str, Any]], error: str = "") -> None:
    sync_runtime(execution_id, {
        "prefect_flow_run_id": prefect_id,
        "status": status,
        "current_step": current_step,
        "total_steps": total_steps,
        "context": {key: value for key, value in context.items() if not key.startswith("_")},
        "error_message": error,
        "step_results": results,
    })


@flow(name="soar-generic")
def run_soar_workflow(run: Dict[str, Any]) -> Dict[str, Any]:
    workflow, definition, trigger = _validated_run(run)
    prefect_id = str(flow_run.id)
    steps = list(definition.get("steps") or [])
    execution_id = str(run.get("execution_id") or "")
    if not execution_id:
        execution_id = register_runtime({
            "workflow_id": str(workflow["id"]),
            "workflow_version": int(workflow.get("version") or 1),
            "prefect_flow_run_id": prefect_id,
            "trigger_source": str(trigger.get("source") or "schedule"),
            "trigger_data": trigger.get("data") or {},
            "total_steps": len(steps),
        })
    logger = get_run_logger()
    logger.info("Running SOAR workflow %s (execution %s)", definition.get("name"), execution_id)
    context: Dict[str, Any] = {
        "trigger_data": trigger.get("data") or {},
        "trigger_source": trigger.get("source") or "manual",
        "execution_id": execution_id,
        "workflow_id": workflow["id"],
        "workflow_name": definition.get("name"),
        "workflow_version": workflow.get("version"),
        "variables": {},
        "step_results": {},
        "previous_step": {},
        "ticket": trigger.get("data") or {},
    }
    if any(
        step.get("node_type") == "action" and step.get("action_type") == "api_call"
        for step in steps
        if isinstance(step, dict)
    ):
        try:
            policy = get_runtime_policy()
            allowlist = policy.get("workflow_http_allowlist") or []
            context["_runtime_policy"] = {
                "workflow_http_allowlist": allowlist if isinstance(allowlist, list) else [],
            }
        except BackendAPIError:
            context["_runtime_policy"] = {"workflow_http_allowlist": []}
            logger.warning(
                "Workflow HTTP runtime policy could not be loaded; all API targets will be denied for this execution."
            )
    results: List[Dict[str, Any]] = []
    _snapshot(execution_id, prefect_id, "running", 0, len(steps), context, results)
    if not steps:
        _snapshot(execution_id, prefect_id, "completed", 0, 0, context, results)
        return {"execution_id": execution_id, "status": "completed", "step_results": results}

    by_id = {str(item["id"]): item for item in steps if isinstance(item, dict) and item.get("id")}
    ordered = sorted(steps, key=lambda item: item.get("order", 0))
    positions = {str(item["id"]): index for index, item in enumerate(ordered) if item.get("id")}
    start = next((item for item in ordered if item.get("node_type") == "start"), ordered[0])
    current = str(start["id"])
    limit, iterations = max(len(ordered) * 5, 1), 0

    def finish_failed(error: str) -> None:
        _snapshot(execution_id, prefect_id, "failed", iterations, len(steps), context, results, error)
        raise RuntimeError(error)

    while current:
        iterations += 1
        if iterations > limit:
            finish_failed("Workflow exceeded max iteration limit; possible loop in graph.")
        step = by_id.get(current)
        if not step:
            finish_failed(f"Workflow references missing step: {current}")
        node_type = step.get("node_type")
        condition_result: bool | None = None
        if node_type in {"start", "end"}:
            result = {"step_id": step["id"], "status": "skipped", "attempt_number": 1, "input_data": {}, "output_data": {}, "error_message": "", "logs": f"skipped {node_type} node"}
        elif node_type == "condition":
            try:
                condition_result = evaluate_condition_object(step.get("condition") or {}, lambda path: resolve_context_path(context, path), context)
                output = {"condition_matched": condition_result}
                result = {"step_id": step["id"], "status": "completed", "attempt_number": 1, "input_data": step.get("condition") or {}, "output_data": output, "error_message": "", "logs": f"Condition evaluated: {condition_result}"}
            except Exception as exc:
                condition_result = False
                result = {"step_id": step["id"], "status": "failed", "attempt_number": 1, "input_data": step.get("condition") or {}, "output_data": {}, "error_message": str(exc), "logs": f"Condition evaluation failed: {exc}"}
        else:
            action_type = str(step.get("action_type") or "")
            if action_type not in ActionRegistry.get_all_actions():
                action_result = {"success": False, "data": {}, "error": f"Unknown action type: {action_type}", "logs": "No action registered"}
            else:
                configured = execute_action_task.with_options(
                    name=str(step.get("name") or action_type),
                    timeout_seconds=int(step.get("timeout_seconds") or 0) or None,
                    retries=max(int(step.get("retry_count") or 0), 0),
                    retry_delay_seconds=max(int(step.get("retry_delay_seconds") or 0), 0),
                )
                try:
                    action_result = configured(action_type, step.get("action_config") or {}, context)
                except Exception as exc:
                    action_result = {"success": False, "data": {}, "error": str(exc), "logs": f"Task execution failed: {exc}"}
            success = bool(action_result.get("success", True))
            output = action_result.get("data") or {}
            result = {"step_id": step["id"], "status": "completed" if success else "failed", "attempt_number": 1, "input_data": step.get("action_config") or {}, "output_data": output, "error_message": action_result.get("error", ""), "logs": action_result.get("logs", "")}

        results.append(result)
        success = result["status"] != "failed"
        output = result.get("output_data") or {}
        context["step_results"][str(step["id"])] = output
        if isinstance(output, dict):
            context["variables"].update(output)
        context["previous_step"] = {"step_id": str(step["id"]), "success": success, "output": output}
        _snapshot(execution_id, prefect_id, "running", iterations, len(steps), context, results)
        if not success and step.get("on_failure") == "stop":
            finish_failed(result.get("error_message") or f"Step '{step.get('name')}' failed.")
        if node_type == "end":
            break
        current = _next_step(ordered, positions, step, condition_result)

    _snapshot(execution_id, prefect_id, "completed", iterations, len(steps), context, results)
    return {"execution_id": execution_id, "status": "completed", "step_results": results}
