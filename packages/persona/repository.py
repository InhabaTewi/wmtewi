from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.persistence.models import PersonaVersion
from packages.schemas.persona import PersonaPackage


class PersonaRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active(self, persona_id: str) -> PersonaVersion | None:
        statement = select(PersonaVersion).where(
            PersonaVersion.persona_id == persona_id,
            PersonaVersion.is_active.is_(True),
        )
        return self.session.scalar(statement)

    def get_version(self, persona_id: str, version: str) -> PersonaVersion | None:
        return self.session.scalar(
            select(PersonaVersion).where(
                PersonaVersion.persona_id == persona_id,
                PersonaVersion.version == version,
            )
        )

    def upsert_version(self, package: PersonaPackage, activate: bool = True) -> PersonaVersion:
        statement = select(PersonaVersion).where(
            PersonaVersion.persona_id == package.persona_id,
            PersonaVersion.version == package.version,
        )
        persona = self.session.scalar(statement)
        if persona is None:
            persona = PersonaVersion(
                persona_id=package.persona_id,
                version=package.version,
                display_name=package.display_name,
                system_prompt=package.system_prompt,
                metadata_=package.metadata,
            )
            self.session.add(persona)
        else:
            persona.display_name = package.display_name
            persona.system_prompt = package.system_prompt
            persona.metadata_ = package.metadata

        if activate:
            self.session.execute(
                update(PersonaVersion)
                .where(PersonaVersion.persona_id == package.persona_id)
                .values(is_active=False)
            )
            persona.is_active = True
        self.session.flush()
        return persona
