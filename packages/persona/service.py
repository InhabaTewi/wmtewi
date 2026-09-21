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
