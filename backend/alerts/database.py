"""Deprecated SQLAlchemy DB helper.

This Django project uses `DATABASES` in `siem_project/settings.py`.
The ES->DB sync flow now uses the Django ORM.

Kept temporarily to avoid breaking any local scripts; safe to delete once
nobody depends on it.
"""

# Intentionally empty.