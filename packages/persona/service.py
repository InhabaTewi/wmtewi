from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from packages.persona.repository import PersonaRepository
from packages.schemas.persona import PersonaPackage


class PersonaService:
    def __init__(self, session: Session) -> None:
        self.repository = PersonaRepository(session)

    def import_yaml(self, path: Path, activate: bool = True) -> PersonaPackage:
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        package = PersonaPackage.model_validate(raw_data)
        self.repository.upsert_version(package, activate=activate)
        return package

    def import_package(self, package: PersonaPackage, *, activate: bool = False) -> str:
        existing = self.repository.get_version(package.persona_id, package.version)
        if existing is not None:
            unchanged = (
                existing.display_name == package.display_name
                and existing.system_prompt == package.system_prompt
                and existing.metadata_ == package.metadata
            )
            if not unchanged:
                raise ValueError(f"persona version conflict: {package.persona_id}/{package.version}")
            if activate and not existing.is_active:
                self.repository.upsert_version(package, activate=True)
                return "activated"
            return "already_exists"
        self.repository.upsert_version(package, activate=activate)
        return "created"
