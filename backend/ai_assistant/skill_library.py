import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from django.conf import settings


@dataclass
class SkillDoc:
    name: str
    description: str
    content: str
    path: str


def _skill_dir() -> str:
    return str(getattr(settings, "AI_SKILLS_DIR", "skills"))


def _find_skill_file(skill_path: str) -> Optional[str]:
    candidates = [
        os.path.join(skill_path, "SKILL.md"),
        os.path.join(skill_path, "skill.md"),
        os.path.join(skill_path, "README.md"),
        os.path.join(skill_path, "readme.md"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _parse_front_matter(content: str) -> Tuple[str, str, str]:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            fm = parts[1]
            body = parts[2].lstrip("\n")
            name = ""
            desc = ""
            for line in fm.splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip("\"'")
            return name, desc, body
    return "", "", content


def list_skills() -> List[str]:
    base = _skill_dir()
    if not os.path.isdir(base):
        return []
    skills: List[str] = []
    for entry in os.listdir(base):
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        if _find_skill_file(full):
            skills.append(entry)
    skills.sort()
    return skills


def read_skill(skill_name: str) -> Optional[SkillDoc]:
    if not skill_name:
        return None
    base = _skill_dir()
    skill_path = os.path.join(base, skill_name)
    if not os.path.isdir(skill_path):
        return None
    skill_file = _find_skill_file(skill_path)
    if not skill_file:
        return None
    with open(skill_file, "r", encoding="utf-8") as f:
        raw = f.read()
    name, desc, body = _parse_front_matter(raw)
    name = name or skill_name
    body = body.strip()
    if not desc:
        # Attempt to derive description from first heading.
        m = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
        if m:
            desc = m.group(1).strip()
    if not body:
        body = desc
    return SkillDoc(name=name, description=desc or "", content=body, path=skill_path)


def read_skill_content(skill_name: str) -> Optional[str]:
    if not skill_name:
        return None
    base = _skill_dir()
    skill_path = os.path.join(base, skill_name)
    if not os.path.isdir(skill_path):
        return None
    skill_file = _find_skill_file(skill_path)
    if not skill_file:
        return None
    with open(skill_file, "r", encoding="utf-8") as f:
        return f.read()


def write_skill_content(
    skill_name: str,
    content: str,
    title: str = "",
    description: str = "",
) -> str:
    base = _skill_dir()
    os.makedirs(base, exist_ok=True)
    skill_path = os.path.join(base, skill_name)
    os.makedirs(skill_path, exist_ok=True)
    target = os.path.join(skill_path, "SKILL.md")

    body = content or ""
    body = body.lstrip("\ufeff")
    if body.strip().startswith("---"):
        payload = body
    else:
        front_matter = ""
        if title or description:
            safe_title = (title or "").replace("\n", " ").strip()
            safe_desc = (description or "").replace("\n", " ").strip()
            front_matter = "---\n"
            if safe_title:
                front_matter += f"name: \"{safe_title}\"\n"
            if safe_desc:
                front_matter += f"description: \"{safe_desc}\"\n"
            front_matter += "---\n\n"
        payload = f"{front_matter}{body}".strip() + "\n"

    with open(target, "w", encoding="utf-8") as f:
        f.write(payload)
    return target
