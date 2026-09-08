import csv
import difflib
import io
import json
import re
import uuid
from itertools import zip_longest

from django.db import transaction

from .models import (
    LocalDetectionDeployment,
    LocalDetectionFieldMapping,
    LocalDetectionRule,
    LocalDetectionRuleMitreAttack,
    LocalDetectionRuleVersion,
)
from .sigma import build_kibana_threat_from_tags, build_rule_detail_metadata, extract_rule_id, extract_rule_meta, parse_mitre_attack_tags


def user_name_from_request(request) -> str:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    return str(getattr(user, "username", "") or getattr(user, "email", "") or getattr(user, "id", "") or "")


def sync_rule_mitre_attack_mappings(rule: LocalDetectionRule, tags: list[str] | None) -> None:
    rows = parse_mitre_attack_tags(tags)
    payload = rule.payload if isinstance(rule.payload, dict) else {}
    kibana_meta = payload.get("kibana_metadata") if isinstance(payload.get("kibana_metadata"), dict) else {}
    sigma_rule_id = str(rule.rule_uuid or "").strip()
    kibana_rule_id = str(payload.get("kibana_rule_id") or payload.get("kibana_remote_id") or kibana_meta.get("remote_id") or "").strip()

    LocalDetectionRuleMitreAttack.objects.filter(rule_id=sigma_rule_id).delete()
    if not rows:
        return
    LocalDetectionRuleMitreAttack.objects.bulk_create(
        [
            LocalDetectionRuleMitreAttack(
                rule_id=sigma_rule_id,
                kibana_rule_id=kibana_rule_id,
                tactic_id=row["tactic_id"],
                tactic_name=row["tactic_name"],
                technique_id=row["technique_id"],
                technique_name=row["technique_name"],
            )
            for row in rows
        ],
        ignore_conflicts=True,
    )

def append_rule_version(
    rule: LocalDetectionRule,
    *,
    version: int,
    payload: dict,
    changed_by: str,
    change_type: str,
    change_summary=None,
) -> None:
    LocalDetectionRuleVersion.objects.create(
        rule=rule,
        version=version,
        payload=payload,
        change_summary=change_summary or [],
        changed_by=changed_by,
        change_type=change_type,
    )


def _truncate(value, limit: int = 140) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _format_summary_value(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        try:
            return _truncate(json.dumps(value, ensure_ascii=False, sort_keys=True), 280)
        except Exception:
            return _truncate(str(value), 280)
    return _truncate(value, 280)


def summarize_payload_changes(previous_payload: dict | None, next_payload: dict | None) -> list[dict]:
    before = previous_payload if isinstance(previous_payload, dict) else {}
    after = next_payload if isinstance(next_payload, dict) else {}
    summary: list[dict] = []

    if str(before.get("yaml") or "") != str(after.get("yaml") or ""):
        before_lines = str(before.get("yaml") or "").splitlines()
        after_lines = str(after.get("yaml") or "").splitlines()
        changed_lines = sum(1 for left, right in zip_longest(before_lines, after_lines, fillvalue="") if left != right)
        yaml_diff = "\n".join(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile="before",
                tofile="after",
                lineterm="",
                n=2,
            )
        )
        summary.append({
            "field": "yaml",
            "label": "Sigma YAML",
            "type": "modified",
            "message": f"Updated Sigma YAML ({changed_lines} changed lines)",
            "diff": _truncate(yaml_diff, 4000),
        })

    tracked = [
        ("severity", "Severity"),
        ("risk_score", "Risk Score"),
        ("name", "Rule Name"),
        ("enabled", "Enabled"),
        ("type", "Rule Type"),
        ("query", "Detection Query"),
        ("esql", "ES|QL Query"),
        ("esql_source", "ES|QL Source"),
        ("description", "Description"),
        ("kibana_metadata", "Kibana Metadata"),
    ]
    for key, label in tracked:
        left = before.get(key)
        right = after.get(key)
        if left != right:
            summary.append({
                "field": key,
                "label": label,
                "type": "modified" if key in before else "added",
                "before": _format_summary_value(left),
                "after": _format_summary_value(right),
                "message": f"{label} changed",
            })

    before_index = before.get("elastic_index_patterns") if isinstance(before.get("elastic_index_patterns"), list) else []
    after_index = after.get("elastic_index_patterns") if isinstance(after.get("elastic_index_patterns"), list) else []
    if before_index != after_index:
        summary.append({
            "field": "elastic_index_patterns",
            "label": "Elastic Index Patterns",
            "type": "modified",
            "before": ", ".join(str(item) for item in before_index),
            "after": ", ".join(str(item) for item in after_index),
            "message": "Elastic Index Patterns changed",
        })

    before_actions = before.get("elastic_actions") if isinstance(before.get("elastic_actions"), list) else []
    after_actions = after.get("elastic_actions") if isinstance(after.get("elastic_actions"), list) else []
    if before_actions != after_actions:
        summary.append({
            "field": "elastic_actions",
            "label": "Elastic Actions",
            "type": "modified",
            "before": f"{len(before_actions)} actions",
            "after": f"{len(after_actions)} actions",
            "message": "Elastic actions configuration changed",
        })

    return summary


def serialize_legacy_rule(rule: LocalDetectionRule) -> dict:
    payload = rule.payload if isinstance(rule.payload, dict) else {}
    # List views must not parse the full YAML document for every rule.  Metadata
    # is materialized when a rule is saved/imported; retain the fallback for
    # older rows until the one-time backfill has completed.
    meta = payload.get("list_meta") if isinstance(payload.get("list_meta"), dict) else None
    if meta is None:
        meta = extract_rule_meta(str(payload.get("yaml") or ""))
    kibana_meta = payload.get("kibana_metadata") if isinstance(payload.get("kibana_metadata"), dict) else {}
    return {
        "id": rule.rule_uuid,
        "name": meta.get("title") or rule.name,
        "level": meta.get("level") or rule.severity or "low",
        "status": meta.get("status") or "draft",
        "logsource": meta.get("logsource") or "",
        "profile": meta.get("profile") or "",
        "tags": meta.get("tags") or [],
        "type": "file",
        "version": rule.version,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
        "publish_status": "published" if kibana_meta.get("published") else "unpublished",
        "kibana_enabled": bool(kibana_meta.get("enabled", False)),
        "kibana_rule_id": str(payload.get("kibana_rule_id") or payload.get("kibana_remote_id") or kibana_meta.get("remote_id") or ""),
    }


def serialize_rule_detail(rule: LocalDetectionRule) -> dict:
    payload = dict(rule.payload or {})
    yaml_text = payload.get("yaml") or json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "id": rule.rule_uuid,
        "name": rule.name,
        "version": rule.version,
        "yaml": yaml_text,
        "payload": payload,
        "meta": build_rule_detail_metadata(yaml_text),
    }


def build_local_rule_payload(
    *,
    rule_id: str,
    yaml_text: str,
    current_rule: LocalDetectionRule | None = None,
    elastic_actions=None,
    elastic_index_patterns=None,
    kibana_metadata=None,
    schedule_interval=None,
    schedule_from=None,
    esql=None,
    esql_source=None,
) -> dict:
    meta = extract_rule_meta(yaml_text)
    sigma_tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    mitre_attack = build_kibana_threat_from_tags(sigma_tags)

    if current_rule:
        payload = dict(current_rule.payload or {})
        payload["id"] = current_rule.rule_uuid
        payload.setdefault("rule_id", current_rule.rule_uuid)
        payload.setdefault("name", current_rule.name)
        payload.setdefault("type", current_rule.rule_type)
        payload.setdefault("enabled", current_rule.enabled)
        payload.setdefault("severity", current_rule.severity)
        payload.setdefault("risk_score", current_rule.risk_score)
    else:
        payload = {
            "id": rule_id or uuid.uuid4().hex,
            "rule_id": rule_id or uuid.uuid4().hex,
            "name": rule_id,
            "type": "query",
            "enabled": False,
            "severity": "low",
            "risk_score": 50,
        }

    payload["yaml"] = yaml_text
    payload["tags"] = sigma_tags
    payload["list_meta"] = {
        "title": str(meta.get("title") or "").strip(),
        "level": str(meta.get("level") or "").strip(),
        "status": str(meta.get("status") or "").strip(),
        "logsource": meta.get("logsource") or "",
        "profile": str(meta.get("profile") or "").strip(),
        "tags": sigma_tags,
    }
    payload["mitre_attack"] = mitre_attack
    if elastic_actions is not None:
        payload["elastic_actions"] = elastic_actions
    if elastic_index_patterns is not None:
        payload["elastic_index_patterns"] = elastic_index_patterns
    if kibana_metadata is not None:
        payload["kibana_metadata"] = kibana_metadata
    if schedule_interval is not None:
        payload["schedule_interval"] = str(schedule_interval)
    if schedule_from is not None:
        payload["schedule_from"] = str(schedule_from)
    if esql_source == "manual":
        payload["esql"] = str(esql or "")
        payload["esql_source"] = "manual"
    elif esql_source == "autogenerated":
        payload.pop("esql", None)
        payload["esql_source"] = "autogenerated"
    return payload


def save_local_rule(
    *,
    rule_id: str,
    yaml_text: str,
    actor: str,
    elastic_actions=None,
    elastic_index_patterns=None,
    kibana_metadata=None,
    schedule_interval=None,
    schedule_from=None,
    esql=None,
    esql_source=None,
) -> LocalDetectionRule:
    with transaction.atomic():
        rule = LocalDetectionRule.objects.filter(rule_uuid=rule_id).first()
        payload = build_local_rule_payload(
            rule_id=rule_id,
            yaml_text=yaml_text,
            current_rule=rule,
            elastic_actions=elastic_actions,
            elastic_index_patterns=elastic_index_patterns,
            kibana_metadata=kibana_metadata,
            schedule_interval=schedule_interval,
            schedule_from=schedule_from,
            esql=esql,
            esql_source=esql_source,
        )

        if not rule:
            rule = LocalDetectionRule.objects.create(
                rule_uuid=payload["id"],
                name=payload["name"],
                enabled=bool(payload.get("enabled", False)),
                rule_type=str(payload.get("type") or "query"),
                severity=str(payload.get("severity") or "low"),
                risk_score=int(payload.get("risk_score") or 50),
                version=1,
                payload=payload,
                created_by=actor,
                updated_by=actor,
                is_deleted=False,
            )
            append_rule_version(
                rule,
                version=1,
                payload=payload,
                changed_by=actor,
                change_type="create",
                change_summary=[{"field": "rule", "label": "Rule", "type": "created", "message": "Created rule"}],
            )
            sync_rule_mitre_attack_mappings(rule, payload.get("tags"))
            return rule

        previous_payload = dict(rule.payload or {})
        rule.version += 1
        rule.payload = payload
        rule.is_deleted = False
        rule.updated_by = actor
        rule.save()
        append_rule_version(
            rule,
            version=rule.version,
            payload=payload,
            changed_by=actor,
            change_type="update",
            change_summary=summarize_payload_changes(previous_payload, payload),
        )
        sync_rule_mitre_attack_mappings(rule, payload.get("tags"))
        return rule


def soft_delete_local_rule(*, rule: LocalDetectionRule, actor: str) -> None:
    with transaction.atomic():
        LocalDetectionRuleMitreAttack.objects.filter(rule_id=rule.rule_uuid).delete()
        rule.is_deleted = True
        rule.version += 1
        rule.updated_by = actor
        rule.save(update_fields=["is_deleted", "version", "updated_by", "updated_at"])
        append_rule_version(
            rule,
            version=rule.version,
            payload=dict(rule.payload or {}),
            changed_by=actor,
            change_type="delete",
            change_summary=[{"field": "rule", "label": "Rule", "type": "deleted", "message": "Deleted rule"}],
        )


def import_rule_files(*, files, actor: str) -> dict:
    created = 0
    updated = 0
    skipped = 0
    results = []

    def import_bundle_entry(entry: dict, source_name: str) -> None:
        nonlocal created, updated, skipped, results
        yaml_text = str(entry.get("yaml") or "").strip()
        rule_id = str(entry.get("id") or entry.get("rule_id") or "").strip() or extract_rule_id(yaml_text)
        if not yaml_text.strip():
            skipped += 1
            results.append({"file": source_name, "status": "skipped", "reason": "bundle entry missing yaml"})
            return
        existed = LocalDetectionRule.objects.filter(rule_uuid=rule_id).exists()
        save_local_rule(
            rule_id=rule_id,
            yaml_text=yaml_text,
            actor=actor,
            elastic_actions=entry.get("elastic_actions") if isinstance(entry.get("elastic_actions"), list) else None,
            elastic_index_patterns=entry.get("elastic_index_patterns") if isinstance(entry.get("elastic_index_patterns"), list) else None,
            kibana_metadata=entry.get("kibana_metadata") if isinstance(entry.get("kibana_metadata"), dict) else None,
        )
        if existed:
            updated += 1
            results.append({"file": source_name, "id": rule_id, "status": "updated"})
        else:
            created += 1
            results.append({"file": source_name, "id": rule_id, "status": "created"})

    for file_obj in files:
        filename = str(getattr(file_obj, "name", "") or "")
        lower_name = filename.lower()
        if lower_name.endswith(".json"):
            try:
                raw = file_obj.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)
            except Exception:
                skipped += 1
                results.append({"file": filename, "status": "skipped", "reason": "invalid json bundle"})
                continue

            entries = data.get("rules") if isinstance(data, dict) else data
            if not isinstance(entries, list):
                skipped += 1
                results.append({"file": filename, "status": "skipped", "reason": "json bundle must contain a rules array"})
                continue

            for index, entry in enumerate(entries):
                if isinstance(entry, dict):
                    import_bundle_entry(entry, f"{filename}#{index + 1}")
                else:
                    skipped += 1
                    results.append({"file": f"{filename}#{index + 1}", "status": "skipped", "reason": "bundle entry is not an object"})
            continue

        if not (lower_name.endswith(".yml") or lower_name.endswith(".yaml")):
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "not yaml"})
            continue

        try:
            yaml_text = file_obj.read().decode("utf-8", errors="ignore")
        except Exception:
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "decode failed"})
            continue

        if not yaml_text.strip():
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "empty file"})
            continue

        rule_id = extract_rule_id(yaml_text)
        existed = LocalDetectionRule.objects.filter(rule_uuid=rule_id).exists()
        save_local_rule(rule_id=rule_id, yaml_text=yaml_text, actor=actor)
        if existed:
            updated += 1
            results.append({"file": filename, "id": rule_id, "status": "updated"})
        else:
            created += 1
            results.append({"file": filename, "id": rule_id, "status": "created"})

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": len(files),
        "results": results,
    }


def import_mapping_files(*, files, actor: str) -> dict:
    created = 0
    updated = 0
    skipped = 0
    results = []

    def parse_index_patterns(value) -> list[str]:
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = re.split(r"[\r\n,;]+", str(value or ""))
        patterns = []
        seen = set()
        for item in raw_values:
            pattern = str(item or "").strip()
            if pattern and pattern not in seen:
                seen.add(pattern)
                patterns.append(pattern)
        return patterns

    def parse_bool(raw) -> bool:
        if isinstance(raw, bool):
            return raw
        value = str(raw or "").strip().lower()
        return value in {"1", "true", "yes", "y", "on"}

    def upsert_row(row: dict) -> str:
        nonlocal created, updated, skipped
        profile = str(row.get("mapping_profile") or row.get("profile") or "").strip()
        if profile == "*":
            profile = "common"
        sigma_field = str(row.get("sigma") or row.get("sigma_field") or "").strip()
        if not profile or not sigma_field:
            skipped += 1
            return "skipped"

        defaults = {
            "category": str(row.get("category") or ""),
            "data_source": str(row.get("data_source") or row.get("datasource") or ""),
            "event_category": str(row.get("event_category") or row.get("event") or ""),
            "splunk_field": str(row.get("splunk") or row.get("splunk_field") or ""),
            "elastic_field": str(row.get("elastic") or row.get("elastic_field") or ""),
            "elastic_is_multivalue": parse_bool(row.get("elastic_is_multivalue") or row.get("multivalue") or row.get("is_multivalue")),
            "elastic_index_patterns": parse_index_patterns(row.get("elastic_index_patterns") or row.get("index_patterns") or row.get("indices")),
            "updated_by": actor,
        }
        obj, is_created = LocalDetectionFieldMapping.objects.update_or_create(
            mapping_profile=profile,
            sigma_field=sigma_field,
            defaults=defaults,
        )
        if is_created:
            obj.created_by = actor
            obj.save(update_fields=["created_by"])
            created += 1
            return "created"

        updated += 1
        return "updated"

    for file_obj in files:
        filename = str(getattr(file_obj, "name", "") or "")
        try:
            text = file_obj.read().decode("utf-8", errors="ignore")
        except Exception:
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "decode failed"})
            continue

        parsed_rows = []
        lower_name = filename.lower()
        if lower_name.endswith(".json"):
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    parsed_rows = [item for item in data if isinstance(item, dict)]
                elif isinstance(data, dict):
                    if isinstance(data.get("mappings"), list):
                        parsed_rows = [item for item in data.get("mappings", []) if isinstance(item, dict)]
                    else:
                        parsed_rows = [data]
            except Exception:
                parsed_rows = []
        elif lower_name.endswith(".csv"):
            try:
                parsed_rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
            except Exception:
                parsed_rows = []
        else:
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "unsupported file type"})
            continue

        if not parsed_rows:
            skipped += 1
            results.append({"file": filename, "status": "skipped", "reason": "no parsable rows"})
            continue

        file_created = 0
        file_updated = 0
        file_skipped = 0
        for row in parsed_rows:
            status = upsert_row(row)
            if status == "created":
                file_created += 1
            elif status == "updated":
                file_updated += 1
            else:
                file_skipped += 1

        results.append(
            {
                "file": filename,
                "status": "ok",
                "rows": len(parsed_rows),
                "created": file_created,
                "updated": file_updated,
                "skipped": file_skipped,
            }
        )

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_files": len(files),
        "results": results,
    }


def export_rule_bundle(*, rule_ids: list[str] | None = None) -> dict:
    rows = LocalDetectionRule.objects.filter(is_deleted=False).order_by("name", "id")
    if rule_ids:
        rows = rows.filter(rule_uuid__in=rule_ids)

    rules = []
    for row in rows:
        payload = dict(row.payload or {})
        rules.append(
            {
                "id": row.rule_uuid,
                "name": row.name,
                "yaml": str(payload.get("yaml") or ""),
                "elastic_actions": payload.get("elastic_actions") if isinstance(payload.get("elastic_actions"), list) else [],
                "elastic_index_patterns": payload.get("elastic_index_patterns") if isinstance(payload.get("elastic_index_patterns"), list) else [],
                "kibana_metadata": payload.get("kibana_metadata") if isinstance(payload.get("kibana_metadata"), dict) else {},
                "version": row.version,
            }
        )
    return {"type": "detection_rule_bundle", "count": len(rules), "rules": rules}


def export_mapping_bundle(*, mapping_ids: list[str] | None = None) -> dict:
    rows = LocalDetectionFieldMapping.objects.order_by("mapping_profile", "sigma_field", "id")
    if mapping_ids:
        rows = rows.filter(id__in=mapping_ids)

    mappings = [
        {
            "id": row.id,
            "mapping_profile": row.mapping_profile,
            "category": row.category,
            "data_source": row.data_source,
            "event_category": row.event_category,
            "sigma": row.sigma_field,
            "splunk": row.splunk_field,
            "elastic": row.elastic_field,
            "elastic_is_multivalue": bool(row.elastic_is_multivalue),
            "elastic_index_patterns": row.elastic_index_patterns if isinstance(row.elastic_index_patterns, list) else [],
        }
        for row in rows
    ]
    return {"type": "detection_mapping_bundle", "count": len(mappings), "mappings": mappings}


def export_mapping_csv(*, mapping_ids: list[str] | None = None) -> str:
    rows = LocalDetectionFieldMapping.objects.order_by("mapping_profile", "sigma_field", "id")
    if mapping_ids:
        rows = rows.filter(id__in=mapping_ids)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["mapping_profile", "category", "data_source", "event_category", "sigma", "splunk", "elastic", "elastic_is_multivalue", "elastic_index_patterns"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "mapping_profile": row.mapping_profile,
                "category": row.category,
                "data_source": row.data_source,
                "event_category": row.event_category,
                "sigma": row.sigma_field,
                "splunk": row.splunk_field,
                "elastic": row.elastic_field,
                "elastic_is_multivalue": "true" if row.elastic_is_multivalue else "false",
                "elastic_index_patterns": ", ".join(row.elastic_index_patterns if isinstance(row.elastic_index_patterns, list) else []),
            }
        )
    return output.getvalue()


def create_deployment_record(
    *,
    rule: LocalDetectionRule,
    actor: str,
    target: str,
    action: str,
    status: str,
    remote_id: str = "",
    remote_rule_id: str = "",
    message: str = "",
    payload: dict | None = None,
) -> LocalDetectionDeployment:
    return LocalDetectionDeployment.objects.create(
        rule=rule,
        rule_name=rule.name,
        target=target,
        action=action,
        status=status,
        remote_id=remote_id,
        remote_rule_id=remote_rule_id,
        message=message,
        payload=payload or {},
        created_by=actor,
    )
