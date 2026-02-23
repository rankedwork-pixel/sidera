"""Role portability --- export, import, and bundle management for Sidera roles.

Enables sharing entire roles across organizations via portable bundles.
A role bundle is a self-contained archive (ZIP or directory) containing:

- ``manifest.yaml`` --- metadata, provenance, compatibility info
- ``_role.yaml`` --- sanitized role definition (no org-specific fields)
- ``skills/`` --- each skill as a sub-bundle (skill.yaml + context/)
- ``_rules.yaml`` --- optional auto-execute rules
- ``memories/seed_memories.yaml`` --- optional seed insight memories
- ``context/`` --- role-level context files

Export sanitizes org-specific fields (``source_dir``, ``steward``,
``document_sync``, ``learning_channels``, ``event_subscriptions``).
Import validates and installs into a target department, creating DB
entries via the Dynamic Org Chart.

Usage::

    from src.skills.role_portability import export_role_to_dir, import_role_from_bundle

    path = export_role_to_dir(role, skills, registry, "/tmp/exports", "admin")
    result = import_role_from_bundle(path, target_dept_id="marketing")
"""

from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.skills.portability import (
    BUNDLE_FORMAT_VERSION,
    _skill_to_portable_dict,
)

# =============================================================================
# Constants
# =============================================================================

# Fields stripped from exported role definitions
_ROLE_EXPORT_STRIP_FIELDS = frozenset(
    {
        "source_dir",
        "context_text",
        "steward",
        "document_sync",
        "learning_channels",
        "event_subscriptions",
    }
)

# =============================================================================
# Manifest dataclass
# =============================================================================


@dataclass
class RoleManifest:
    """Metadata for a portable role bundle.

    Attributes:
        format_version: Bundle format version.
        role_id: The role's unique identifier.
        role_name: Human-readable role name.
        description: Brief description of what the role does.
        author: Who exported the bundle.
        exported_at: ISO timestamp of export.
        exported_by: User or system that performed the export.
        sha256: SHA-256 hash of _role.yaml for integrity.
        skills: List of included skill IDs.
        has_rules: Whether auto-execute rules are included.
        has_seed_memories: Whether seed memories are included.
        skill_count: Number of skills in the bundle.
        compatibility: Compatibility metadata.
        provenance: Source org / export reason.
    """

    format_version: str = BUNDLE_FORMAT_VERSION
    role_id: str = ""
    role_name: str = ""
    description: str = ""
    author: str = ""
    exported_at: str = ""
    exported_by: str = ""
    sha256: str = ""
    skills: list[str] = field(default_factory=list)
    has_rules: bool = False
    has_seed_memories: bool = False
    skill_count: int = 0
    compatibility: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Import result
# =============================================================================


@dataclass
class RoleImportResult:
    """Result of importing a role bundle.

    Attributes:
        success: Whether the import succeeded.
        role_id: The imported role's ID.
        role_name: The imported role's name.
        target_department_id: Department it was installed into.
        skills_imported: List of skill IDs that were imported.
        rules_imported: Whether auto-execute rules were imported.
        seed_memories_count: Number of seed memories imported.
        errors: List of error messages if import failed.
        warnings: List of non-fatal warnings.
    """

    success: bool = False
    role_id: str = ""
    role_name: str = ""
    target_department_id: str = ""
    skills_imported: list[str] = field(default_factory=list)
    rules_imported: bool = False
    seed_memories_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# Export helpers
# =============================================================================


def _role_to_portable_dict(role: Any) -> dict[str, Any]:
    """Convert a RoleDefinition to a portable dict, stripping org fields.

    Args:
        role: A ``RoleDefinition`` frozen dataclass instance.

    Returns:
        Dict suitable for YAML serialization.
    """
    d = asdict(role) if hasattr(role, "__dataclass_fields__") else dict(role)

    for key in _ROLE_EXPORT_STRIP_FIELDS:
        d.pop(key, None)

    # Also strip department_id (set by target on import)
    d.pop("department_id", None)

    # Convert tuples to lists for YAML readability
    tuple_fields = (
        "connectors",
        "briefing_skills",
        "principles",
        "goals",
        "manages",
        "routing_keywords",
        "context_files",
    )
    for key in tuple_fields:
        if key in d and isinstance(d[key], tuple):
            d[key] = list(d[key])

    # Remove empty/None optional fields for cleaner YAML
    remove_keys = [
        k
        for k, v in d.items()
        if v in (None, "", [], (), 0, False) and k not in ("id", "name", "description", "persona")
    ]
    for k in remove_keys:
        del d[k]

    return d


def _build_role_manifest(
    role: Any,
    skill_ids: list[str],
    role_yaml_hash: str,
    has_rules: bool,
    has_seed_memories: bool,
    exported_by: str = "sidera",
) -> RoleManifest:
    """Build a manifest for a role bundle."""
    return RoleManifest(
        format_version=BUNDLE_FORMAT_VERSION,
        role_id=getattr(role, "id", ""),
        role_name=getattr(role, "name", ""),
        description=getattr(role, "description", ""),
        author=exported_by,
        exported_at=datetime.now(timezone.utc).isoformat(),
        exported_by=exported_by,
        sha256=role_yaml_hash,
        skills=skill_ids,
        has_rules=has_rules,
        has_seed_memories=has_seed_memories,
        skill_count=len(skill_ids),
        compatibility={
            "min_format_version": "1.0",
            "required_connectors": list(getattr(role, "connectors", ()) or ()),
        },
        provenance={
            "original_department": getattr(role, "department_id", None),
        },
    )


# =============================================================================
# Export functions
# =============================================================================


def export_role_to_dir(
    role: Any,
    skills: list[Any],
    output_dir: str | Path,
    exported_by: str = "sidera",
    include_rules: bool = True,
    include_memories: list[dict[str, Any]] | None = None,
) -> Path:
    """Export a role as a portable directory bundle.

    Creates a directory at ``output_dir/<role_id>/`` containing the role
    definition, all its skills, and optional rules / seed memories.

    Args:
        role: The ``RoleDefinition`` to export.
        skills: List of ``SkillDefinition`` objects for this role.
        output_dir: Parent directory for the bundle.
        exported_by: Attribution for who exported.
        include_rules: Whether to include ``_rules.yaml`` if it exists.
        include_memories: Optional list of memory dicts to include as
            seed memories (each must have ``title``, ``content``,
            ``memory_type``).

    Returns:
        Path to the created bundle directory.
    """
    output_dir = Path(output_dir)
    role_id = getattr(role, "id", "unknown_role")
    bundle_dir = output_dir / role_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export role YAML
    portable = _role_to_portable_dict(role)
    role_yaml = yaml.dump(
        portable,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    role_yaml_path = bundle_dir / "_role.yaml"
    role_yaml_path.write_text(role_yaml, encoding="utf-8")

    # 2. Export skills as sub-bundles
    skills_dir = bundle_dir / "skills"
    skill_ids: list[str] = []
    for skill in skills:
        sid = getattr(skill, "id", "")
        if not sid:
            continue
        skill_ids.append(sid)

        skill_bundle_dir = skills_dir / sid
        skill_bundle_dir.mkdir(parents=True, exist_ok=True)

        # Write skill.yaml
        skill_portable = _skill_to_portable_dict(skill)
        skill_yaml_text = yaml.dump(
            skill_portable,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        (skill_bundle_dir / "skill.yaml").write_text(skill_yaml_text, encoding="utf-8")

        # Copy context files
        source_dir = getattr(skill, "source_dir", "")
        context_files = getattr(skill, "context_files", ())
        if source_dir and context_files:
            src_path = Path(source_dir)
            ctx_dir = skill_bundle_dir / "context"
            for pattern in context_files:
                for src_file in sorted(src_path.glob(pattern)):
                    if src_file.is_file():
                        rel = src_file.relative_to(src_path)
                        dst = ctx_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst)

    # 3. Copy role context files
    role_source_dir = getattr(role, "source_dir", "")
    role_context_files = getattr(role, "context_files", ())
    if role_source_dir and role_context_files:
        src_path = Path(role_source_dir)
        ctx_dir = bundle_dir / "context"
        for pattern in role_context_files:
            for src_file in sorted(src_path.glob(pattern)):
                if src_file.is_file():
                    rel = src_file.relative_to(src_path)
                    dst = ctx_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst)

    # 4. Copy auto-execute rules if they exist
    has_rules = False
    if include_rules and role_source_dir:
        rules_file = Path(role_source_dir) / "_rules.yaml"
        if rules_file.exists():
            shutil.copy2(rules_file, bundle_dir / "_rules.yaml")
            has_rules = True

    # 5. Write seed memories if provided
    has_seed_memories = False
    if include_memories:
        mem_dir = bundle_dir / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        mem_yaml = yaml.dump(
            include_memories,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        (mem_dir / "seed_memories.yaml").write_text(mem_yaml, encoding="utf-8")
        has_seed_memories = True

    # 6. Build and write manifest
    sha256 = hashlib.sha256(role_yaml.encode()).hexdigest()
    manifest = _build_role_manifest(
        role, skill_ids, sha256, has_rules, has_seed_memories, exported_by
    )
    manifest_yaml = yaml.dump(
        asdict(manifest),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    (bundle_dir / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")

    return bundle_dir


def export_role_to_zip(
    role: Any,
    skills: list[Any],
    output_path: str | Path,
    exported_by: str = "sidera",
    include_rules: bool = True,
    include_memories: list[dict[str, Any]] | None = None,
) -> Path:
    """Export a role as a ZIP archive.

    Creates a temporary directory bundle, then zips it.

    Args:
        role: The ``RoleDefinition`` to export.
        skills: List of ``SkillDefinition`` objects.
        output_path: Path for the output ZIP file.
        exported_by: Attribution for who exported.
        include_rules: Whether to include ``_rules.yaml``.
        include_memories: Optional seed memories.

    Returns:
        Path to the created ZIP file.
    """
    import tempfile

    output_path = Path(output_path)

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = export_role_to_dir(
            role=role,
            skills=skills,
            output_dir=tmp,
            exported_by=exported_by,
            include_rules=include_rules,
            include_memories=include_memories,
        )

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(Path(tmp))
                    zf.write(file_path, arcname)

    return output_path


def export_role_to_bytes(
    role: Any,
    skills: list[Any],
    exported_by: str = "sidera",
    include_rules: bool = True,
    include_memories: list[dict[str, Any]] | None = None,
) -> bytes:
    """Export a role as in-memory ZIP bytes.

    Args:
        role: The ``RoleDefinition`` to export.
        skills: List of ``SkillDefinition`` objects.
        exported_by: Attribution for who exported.
        include_rules: Whether to include ``_rules.yaml``.
        include_memories: Optional seed memories.

    Returns:
        Raw ZIP file bytes.
    """
    import tempfile

    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = export_role_to_dir(
            role=role,
            skills=skills,
            output_dir=tmp,
            exported_by=exported_by,
            include_rules=include_rules,
            include_memories=include_memories,
        )

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(Path(tmp))
                    zf.write(file_path, arcname)

    return buf.getvalue()


# =============================================================================
# Validation
# =============================================================================


def validate_role_bundle(
    bundle_path: str | Path,
) -> RoleImportResult:
    """Validate a role bundle (directory or ZIP).

    Checks:
    - Contains ``_role.yaml`` and ``manifest.yaml``
    - ``_role.yaml`` has required fields (id, name, persona)
    - SHA-256 integrity check
    - Skills directory has valid skill.yaml files

    Args:
        bundle_path: Path to the bundle directory or ZIP.

    Returns:
        ``RoleImportResult`` with errors/warnings populated.
        ``success`` is True only if validation passes.
    """
    import tempfile

    bundle_path = Path(bundle_path)
    result = RoleImportResult()

    # Handle ZIP
    if bundle_path.suffix == ".zip" or zipfile.is_zipfile(bundle_path):
        try:
            tmp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(bundle_path, "r") as zf:
                zf.extractall(tmp_dir)
            # Find the bundle root (should be a single subdirectory)
            subdirs = [d for d in Path(tmp_dir).iterdir() if d.is_dir()]
            if subdirs:
                bundle_root = subdirs[0]
            else:
                bundle_root = Path(tmp_dir)
        except Exception as exc:
            result.errors.append(f"Cannot extract ZIP: {exc}")
            return result
    else:
        bundle_root = bundle_path

    # Check required files
    role_yaml_path = bundle_root / "_role.yaml"
    manifest_path = bundle_root / "manifest.yaml"

    if not role_yaml_path.exists():
        result.errors.append("Missing _role.yaml")
    if not manifest_path.exists():
        result.errors.append("Missing manifest.yaml")

    if result.errors:
        return result

    # Parse role definition
    try:
        role_data = yaml.safe_load(role_yaml_path.read_text(encoding="utf-8"))
        if not isinstance(role_data, dict):
            result.errors.append("_role.yaml is not a valid YAML dict")
            return result
    except Exception as exc:
        result.errors.append(f"Cannot parse _role.yaml: {exc}")
        return result

    # Check required role fields
    if not role_data.get("id"):
        result.errors.append("_role.yaml missing 'id' field")
    if not role_data.get("name"):
        result.errors.append("_role.yaml missing 'name' field")
    if not role_data.get("persona"):
        result.warnings.append("_role.yaml missing 'persona' field")

    result.role_id = role_data.get("id", "")
    result.role_name = role_data.get("name", "")

    # SHA-256 integrity check
    try:
        manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if isinstance(manifest_data, dict) and manifest_data.get("sha256"):
            actual_hash = hashlib.sha256(role_yaml_path.read_bytes()).hexdigest()
            if actual_hash != manifest_data["sha256"]:
                result.errors.append(
                    f"SHA-256 mismatch: expected {manifest_data['sha256'][:16]}... "
                    f"got {actual_hash[:16]}..."
                )
    except Exception:
        result.warnings.append("Cannot verify manifest integrity")

    # Validate skills
    skills_dir = bundle_root / "skills"
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_yaml = skill_dir / "skill.yaml"
            if not skill_yaml.exists():
                result.warnings.append(f"Skill dir {skill_dir.name} missing skill.yaml")
                continue
            try:
                skill_data = yaml.safe_load(skill_yaml.read_text(encoding="utf-8"))
                if isinstance(skill_data, dict) and skill_data.get("id"):
                    result.skills_imported.append(skill_data["id"])
                else:
                    result.warnings.append(f"Skill {skill_dir.name}/skill.yaml missing 'id'")
            except Exception as exc:
                result.warnings.append(f"Cannot parse {skill_dir.name}/skill.yaml: {exc}")

    if not result.errors:
        result.success = True

    return result


# =============================================================================
# Import
# =============================================================================


async def import_role_from_bundle(
    bundle_path: str | Path,
    target_department_id: str = "",
    new_role_id: str = "",
    new_author: str = "",
    user_id: str = "system",
) -> RoleImportResult:
    """Import a role from a portable bundle.

    Validates the bundle, creates the role in the DB via
    ``create_org_role()``, imports each skill, and optionally loads
    auto-execute rules and seed memories.

    Args:
        bundle_path: Path to the bundle directory or ZIP.
        target_department_id: Department to install the role into.
            If empty, uses the original from the bundle.
        new_role_id: Override the role ID (fork). If empty, uses
            the original ID.
        new_author: Override the author. If empty, keeps original.
        user_id: User ID for memory ownership. Defaults to "system".

    Returns:
        ``RoleImportResult`` with import status.
    """
    import tempfile

    from src.db import service as db_service
    from src.db.session import get_db_session

    bundle_path = Path(bundle_path)

    # Validate first
    validation = validate_role_bundle(bundle_path)
    if not validation.success:
        return validation

    result = RoleImportResult()

    # Resolve bundle root
    tmp_dir = None
    if bundle_path.suffix == ".zip" or zipfile.is_zipfile(bundle_path):
        tmp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(tmp_dir)
        subdirs = [d for d in Path(tmp_dir).iterdir() if d.is_dir()]
        bundle_root = subdirs[0] if subdirs else Path(tmp_dir)
    else:
        bundle_root = bundle_path

    try:
        # Load role data
        role_data = yaml.safe_load((bundle_root / "_role.yaml").read_text(encoding="utf-8"))

        role_id = new_role_id or role_data.get("id", "")
        if not role_id:
            result.errors.append("No role ID available")
            return result

        dept_id = target_department_id or role_data.get("department_id", "")
        result.role_id = role_id
        result.role_name = role_data.get("name", "")
        result.target_department_id = dept_id

        # Create role in DB
        async with get_db_session() as session:
            await db_service.create_org_role(
                session,
                role_id=role_id,
                name=role_data.get("name", role_id),
                department_id=dept_id,
                description=role_data.get("description", ""),
                persona=role_data.get("persona", ""),
                connectors=role_data.get("connectors"),
                briefing_skills=role_data.get("briefing_skills"),
                schedule=role_data.get("schedule"),
                manages=role_data.get("manages"),
                delegation_model=role_data.get("delegation_model", "standard"),
                synthesis_prompt=role_data.get("synthesis_prompt", ""),
                created_by=new_author or "imported",
            )

        # Import skills
        skills_dir = bundle_root / "skills"
        if skills_dir.exists():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_yaml_path = skill_dir / "skill.yaml"
                if not skill_yaml_path.exists():
                    continue

                try:
                    skill_data = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8"))
                    if not isinstance(skill_data, dict):
                        continue

                    sid = skill_data.get("id", skill_dir.name)

                    # Gather context text from context files
                    ctx_dir = skill_dir / "context"
                    context_text = ""
                    if ctx_dir.exists():
                        parts = []
                        for ctx_file in sorted(ctx_dir.rglob("*")):
                            if ctx_file.is_file():
                                try:
                                    parts.append(ctx_file.read_text(encoding="utf-8"))
                                except Exception:
                                    pass
                        context_text = "\n\n".join(parts)

                    async with get_db_session() as session:
                        await db_service.create_org_skill(
                            session,
                            skill_id=sid,
                            name=skill_data.get("name", sid),
                            description=skill_data.get("description", ""),
                            category=skill_data.get("category", "analysis"),
                            system_supplement=skill_data.get("system_supplement", ""),
                            prompt_template=skill_data.get("prompt_template", ""),
                            output_format=skill_data.get("output_format", ""),
                            business_guidance=skill_data.get("business_guidance", ""),
                            platforms=skill_data.get("platforms"),
                            tags=skill_data.get("tags"),
                            tools_required=skill_data.get("tools_required"),
                            model=skill_data.get("model", "sonnet"),
                            max_turns=skill_data.get("max_turns", 20),
                            context_text=context_text,
                            department_id=dept_id,
                            role_id=role_id,
                            author=new_author or skill_data.get("author", "imported"),
                            created_by=new_author or "imported",
                        )
                    result.skills_imported.append(sid)
                except Exception as exc:
                    result.warnings.append(f"Skill {skill_dir.name} import failed: {exc}")

        # Import seed memories
        seed_path = bundle_root / "memories" / "seed_memories.yaml"
        if seed_path.exists():
            try:
                memories = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
                if isinstance(memories, list):
                    for mem in memories:
                        if not isinstance(mem, dict):
                            continue
                        async with get_db_session() as session:
                            await db_service.save_memory(
                                session,
                                user_id=user_id,
                                role_id=role_id,
                                department_id=dept_id,
                                memory_type=mem.get("memory_type", "insight"),
                                title=mem.get("title", "")[:100],
                                content=mem.get("content", "")[:500],
                                confidence=mem.get("confidence", 0.6),
                                evidence={
                                    "source": "role_bundle_import",
                                    "original_role_id": role_data.get("id", ""),
                                },
                            )
                        result.seed_memories_count += 1
            except Exception as exc:
                result.warnings.append(f"Seed memories import failed: {exc}")

        result.success = True

    except Exception as exc:
        result.errors.append(f"Import failed: {exc}")

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


# =============================================================================
# Discovery
# =============================================================================


def list_role_bundles_in_dir(
    directory: str | Path,
) -> list[RoleManifest]:
    """List all role bundles in a directory.

    Scans for subdirectories containing ``manifest.yaml`` and
    ``_role.yaml``.

    Args:
        directory: Directory to scan.

    Returns:
        List of parsed ``RoleManifest`` objects.
    """
    directory = Path(directory)
    if not directory.exists():
        return []

    manifests: list[RoleManifest] = []
    for sub in sorted(directory.iterdir()):
        if not sub.is_dir():
            continue
        manifest_path = sub / "manifest.yaml"
        role_path = sub / "_role.yaml"
        if not manifest_path.exists() or not role_path.exists():
            continue

        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            manifests.append(
                RoleManifest(
                    format_version=data.get("format_version", BUNDLE_FORMAT_VERSION),
                    role_id=data.get("role_id", ""),
                    role_name=data.get("role_name", ""),
                    description=data.get("description", ""),
                    author=data.get("author", ""),
                    exported_at=data.get("exported_at", ""),
                    exported_by=data.get("exported_by", ""),
                    sha256=data.get("sha256", ""),
                    skills=data.get("skills", []),
                    has_rules=data.get("has_rules", False),
                    has_seed_memories=data.get("has_seed_memories", False),
                    skill_count=data.get("skill_count", 0),
                    compatibility=data.get("compatibility", {}),
                    provenance=data.get("provenance", {}),
                )
            )
        except Exception:
            continue

    return manifests


def search_role_bundles(
    bundles: list[RoleManifest],
    query: str = "",
    department: str = "",
) -> list[RoleManifest]:
    """Search role bundles by query text and/or department.

    Args:
        bundles: List of ``RoleManifest`` objects to search.
        query: Free-text search (matches name, description, skills).
        department: Department filter (matches provenance).

    Returns:
        Filtered list of matching bundles.
    """
    results: list[RoleManifest] = []
    query_lower = query.lower()

    for bundle in bundles:
        # Department filter
        if department:
            orig_dept = bundle.provenance.get("original_department", "")
            if department.lower() not in str(orig_dept).lower():
                continue

        # Query filter
        if query_lower:
            searchable = " ".join(
                [
                    bundle.role_id,
                    bundle.role_name,
                    bundle.description,
                ]
                + bundle.skills
            ).lower()
            if query_lower not in searchable:
                continue

        results.append(bundle)

    return results
