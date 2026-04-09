from typing import Any, Dict, List

from tickets.models import EventTicket
from ai_assistant.monitoring import record_skill_call


def _enabled_routes(skills: List[Dict[str, Any]] | None) -> set[str]:
    routes: set[str] = set()
    if not isinstance(skills, list):
        return routes
    for s in skills:
        if not isinstance(s, dict):
            continue
        if s.get("enabled") is False:
            continue
        route = str(s.get("route") or s.get("name") or "").strip()
        if route:
            routes.add(route)
    return routes


def _apply_soc_ticket_triage(
    assistant: Dict[str, Any],
    ticket: EventTicket,
    timeline: List[Dict[str, Any]],
) -> None:
    if not isinstance(assistant.get("header"), dict):
        assistant["header"] = {}
    header = assistant["header"]

    risk_level = str(getattr(ticket, "priority", "") or "medium").lower()
    if risk_level not in ("critical", "high", "medium", "low", "info"):
        risk_level = "medium"
    header.setdefault("risk_level", risk_level)
    header.setdefault("ai_confidence", "0.70")
    header.setdefault("score", int(getattr(ticket, "event_risk_score", 0) or 0))
    header.setdefault("summary_title", str(getattr(ticket, "title", "") or "SOC triage summary"))

    if not isinstance(assistant.get("risk_level_recommendation"), dict):
        assistant["risk_level_recommendation"] = {
            "level": risk_level,
            "rationale": "Derived from ticket priority and available context.",
        }

    if not isinstance(assistant.get("completed_tasks"), list):
        assistant["completed_tasks"] = []
    if not isinstance(assistant.get("next_tasks"), list):
        assistant["next_tasks"] = []

    if not assistant["next_tasks"]:
        assistant["next_tasks"] = [
            {"title": "Validate affected scope", "detail": "Confirm impacted hosts/users from ticket context."},
            {"title": "Correlate recent logs", "detail": "Review recent timeline and related alerts for recurrence."},
            {"title": "Prepare containment plan", "detail": "Draft immediate containment actions for approval."},
        ]

    if not isinstance(assistant.get("alert_explanation"), str):
        assistant["alert_explanation"] = "Initial triage completed using ticket context, timeline, and similar cases."


def _apply_incident_summary(
    assistant: Dict[str, Any],
    ticket: EventTicket,
    timeline: List[Dict[str, Any]],
) -> None:
    if not isinstance(assistant.get("case_summary"), dict):
        assistant["case_summary"] = {}
    summary = assistant["case_summary"]
    summary.setdefault("incident_summary", str(getattr(ticket, "description", "") or getattr(ticket, "title", "") or ""))
    summary.setdefault("timeline", timeline if isinstance(timeline, list) else [])
    summary.setdefault("impact_assessment", str(getattr(ticket, "event_impact", "") or "Pending validation."))
    summary.setdefault("root_cause", "Under investigation.")
    summary.setdefault("remediation_recommendations", ["Complete triage checklist and confirm containment actions."])


def apply_local_skills(
    assistant: Dict[str, Any] | None,
    ticket: EventTicket,
    timeline: List[Dict[str, Any]],
    skills: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any] | None:
    if not isinstance(assistant, dict):
        return assistant

    routes = _enabled_routes(skills)
    if not routes:
        return assistant

    applied_routes: set[str] = set()
    if "ticket_triage" in routes or "soc-ticket-triage" in routes:
        _apply_soc_ticket_triage(assistant, ticket=ticket, timeline=timeline)
        applied_routes.add("ticket_triage")
        applied_routes.add("soc-ticket-triage")
    if "incident_summary" in routes or "incident-summary" in routes:
        _apply_incident_summary(assistant, ticket=ticket, timeline=timeline)
        applied_routes.add("incident_summary")
        applied_routes.add("incident-summary")

    if isinstance(skills, list):
        for s in skills:
            if not isinstance(s, dict):
                continue
            if s.get("enabled") is False:
                continue
            route = str(s.get("route") or s.get("name") or "").strip()
            name = str(s.get("name") or route).strip()
            if not name:
                continue
            if route in applied_routes:
                record_skill_call(name, True)
    return assistant
