from __future__ import annotations

from typing import Iterable, List, Dict, Any

from django.core.management.base import BaseCommand
from django.db import transaction

from workflows.models import Workflow, WorkflowStep


def _safe_str(value: Any) -> str:
    return str(value) if value is not None else ''


def _build_edges_from_steps(steps: Iterable[WorkflowStep]) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []
    step_list = list(steps)
    step_id_set = {str(step.id) for step in step_list}

    def add_edge(source: str, target: str, source_handle: str | None = None, label: str | None = None) -> None:
        if not source or not target:
            return
        if source not in step_id_set or target not in step_id_set:
            return
        edge_id = f"norm_{source}_{target}_{len(edges)}"
        edges.append({
            "id": edge_id,
            "source": source,
            "target": target,
            "sourceHandle": source_handle,
            "targetHandle": None,
            "label": label,
        })

    for step in step_list:
        step_id = str(step.id)
        if step.node_type == "condition":
            if step.next_step_true:
                add_edge(step_id, _safe_str(step.next_step_true), "true", "Yes")
            if step.next_step_false:
                add_edge(step_id, _safe_str(step.next_step_false), "false", "No")

        if isinstance(step.connections, list):
            for target in step.connections:
                add_edge(step_id, _safe_str(target))

    if not edges and len(step_list) > 1:
        ordered = sorted(step_list, key=lambda s: s.order)
        for idx in range(len(ordered) - 1):
            add_edge(str(ordered[idx].id), str(ordered[idx + 1].id))

    return edges


def _filter_valid_edges(edges: List[Dict[str, Any]], step_ids: set[str]) -> List[Dict[str, Any]]:
    valid = []
    for edge in edges:
        source = _safe_str(edge.get("source"))
        target = _safe_str(edge.get("target"))
        if source in step_ids and target in step_ids:
            valid.append(edge)
    return valid


class Command(BaseCommand):
    help = "Normalize workflow edges to match step IDs and restore visual arrows."

    def add_arguments(self, parser):
        parser.add_argument("--workflow-id", dest="workflow_id", help="Limit to a single workflow UUID")
        parser.add_argument("--dry-run", action="store_true", help="Show changes without saving")
        parser.add_argument("--force", action="store_true", help="Rebuild edges even if current edges look valid")

    def handle(self, *args, **options):
        workflow_id = options.get("workflow_id")
        dry_run = options.get("dry_run", False)
        force = options.get("force", False)

        workflows = Workflow.objects.all()
        if workflow_id:
            workflows = workflows.filter(id=workflow_id)

        updated = 0
        skipped = 0

        for workflow in workflows:
            steps = list(workflow.steps.all())
            step_ids = {str(step.id) for step in steps}
            existing_edges = workflow.edges or []

            valid_edges = _filter_valid_edges(existing_edges, step_ids)
            should_rebuild = force or (not valid_edges and steps)

            if not should_rebuild and len(valid_edges) == len(existing_edges):
                skipped += 1
                continue

            if should_rebuild:
                new_edges = _build_edges_from_steps(steps)
            else:
                new_edges = valid_edges

            if dry_run:
                updated += 1
                self.stdout.write(
                    f"[DRY RUN] {workflow.id} '{workflow.name}': {len(existing_edges)} -> {len(new_edges)} edges"
                )
                continue

            with transaction.atomic():
                workflow.edges = new_edges
                workflow.save(update_fields=["edges", "updated_at"])
            updated += 1
            self.stdout.write(
                f"Updated {workflow.id} '{workflow.name}': {len(existing_edges)} -> {len(new_edges)} edges"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Normalization complete. Updated: {updated}, Skipped: {skipped}"
        ))

