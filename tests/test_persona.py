from pathlib import Path

import yaml

from packages.persona.service import PersonaService


def test_yaml_import_activates_and_versions_persona(session) -> None:
    package_path = Path("configs/persona/inaba.yaml")
    service = PersonaService(session)

    imported = service.import_yaml(package_path)
    session.commit()
    active = service.repository.get_active("inaba")

    assert imported.version == "inaba-1"
    assert active is not None
    assert active.version == "inaba-1"
    assert active.system_prompt


def test_importing_new_persona_version_switches_the_active_version(session, tmp_path) -> None:
    service = PersonaService(session)
    service.import_yaml(Path("configs/persona/inaba.yaml"))
    newer_package = tmp_path / "inaba-v2.yaml"
    newer_package.write_text(
        yaml.safe_dump(
            {
                "persona_id": "inaba",
                "version": "inaba-2",
                "display_name": "因幡未梦",
                "system_prompt": "Version two persona instructions.",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    service.import_yaml(newer_package)
    session.commit()
    active = service.repository.get_active("inaba")

    assert active is not None
    assert active.version == "inaba-2"
    assert active.system_prompt == "Version two persona instructions."
