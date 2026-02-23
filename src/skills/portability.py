"""Skill portability — export, import, and bundle management for Sidera skills.

Enables sharing skills across organizations via portable bundles.  A bundle
is a self-contained archive (ZIP or directory) containing:

- ``manifest.yaml`` — metadata, provenance, compatibility info
- ``skill.yaml`` — the skill definition (sanitized, no org-specific fields)
- ``context/`` — context files (examples, guidelines, reference material)

Export sanitizes org-specific fields (``source_dir``, ``context_text``,
``department_id``, ``role_id``).  Import validates and installs into a
target department/role, creating DB entries via the Dynamic Org Chart.

Usage::

    from src.skills.portability import export_skill, import_skill

    # Export a skill from the registry
    bundle_path = await export_skill(
        skill_id="creative_analysis",
        registry=registry,
        output_dir="/tmp/exports",
    )

    # Import a skill from a bundle
    result = await import_skill(
        bundle_path=bundle_path,
        target_department_id="marketing",
        target_role_id="performance_media_buyer",
    )
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

from src.skills.schema import (
    SkillDefinition,
    validate_skill,
)

# =============================================================================
# Constants
# =============================================================================

# Current bundle format version
BUNDLE_FORMAT_VERSION = "1.0"

# Fields stripped from exported skill definitions
_EXPORT_STRIP_FIELDS = frozenset(
    {
        "source_dir",
        "context_text",
        "department_id",
        "role_id",
    }
)

# Fields that consumers typically customize after import
_CUSTOMIZABLE_FIELDS = frozenset(
    {
        "schedule",
        "chain_after",
        "min_clearance",
        "requires_approval",
    }
)


# =============================================================================
# Manifest dataclass
# =============================================================================


@dataclass
class SkillManifest:
    """Metadata for a portable skill bundle.

    Attributes:
        format_version: Bundle format version (for future compat).
        skill_id: The skill's unique identifier.
        skill_name: Human-readable skill name.
        skill_version: Semantic version of the skill.
        description: Brief description of what the skill does.
        author: Original creator of the skill.
        category: Skill category (analysis, monitoring, etc.).
        platforms: Required platform integrations.
        tags: Searchable tags.
        exported_at: ISO timestamp of export.
        exported_by: User or system that performed the export.
        sha256: SHA-256 hash of the skill.yaml for integrity.
        context_files: List of included context file paths.
        compatibility: Compatibility requirements.
        provenance: Optional provenance tracking metadata.
    """

    format_version: str = BUNDLE_FORMAT_VERSION
    skill_id: str = ""
    skill_name: str = ""
    skill_version: str = ""
    description: str = ""
    author: str = ""
    category: str = ""
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exported_at: str = ""
    exported_by: str = ""
    sha256: str = ""
    context_files: list[str] = field(default_factory=list)
    compatibility: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Import result
# =============================================================================


@dataclass
class ImportResult:
    """Result of importing a skill bundle.

    Attributes:
        success: Whether the import succeeded.
        skill_id: The imported skill's ID.
        skill_name: The imported skill's name.
        target_department_id: Department it was installed into.
        target_role_id: Role it was installed into.
        errors: List of error messages if import failed.
        warnings: List of non-fatal warnings.
        context_files_count: Number of context files imported.
    """

    success: bool = False
    skill_id: str = ""
    skill_name: str = ""
    target_department_id: str = ""
    target_role_id: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context_files_count: int = 0


# =============================================================================
# Export
# =============================================================================


def _skill_to_portable_dict(skill: SkillDefinition) -> dict[str, Any]:
    """Convert a SkillDefinition to a portable dict, stripping org fields."""
    d = asdict(skill)

    for key in _EXPORT_STRIP_FIELDS:
        d.pop(key, None)

    # Convert tuples to lists for YAML readability
    for key in (
        "platforms",
        "tags",
        "tools_required",
        "context_files",
    ):
        if key in d and isinstance(d[key], tuple):
            d[key] = list(d[key])

    # Convert context_file_descriptions tuple-of-tuples to list-of-dicts
    if "context_file_descriptions" in d:
        cfd = d["context_file_descriptions"]
        if cfd:
            d["context_file_descriptions"] = [
                {"pattern": p, "description": desc} for p, desc in cfd
            ]
        else:
            del d["context_file_descriptions"]

    # Convert references tuple-of-tuples to list-of-dicts
    if "references" in d:
        refs = d["references"]
        if refs:
            d["references"] = [
                {"skill_id": sid, "relationship": rel, "reason": reason}
                for sid, rel, reason in refs
            ]
        else:
            del d["references"]

    # Remove empty/None optional fields for cleaner YAML
    remove_keys = [
        k
        for k, v in d.items()
        if v in (None, "", [], (), 0, False)
        and k
        not in (
            "id",
            "name",
            "version",
            "description",
            "category",
            "model",
            "system_supplement",
            "prompt_template",
            "output_format",
            "business_guidance",
            "requires_approval",
        )
    ]
    for k in remove_keys:
        del d[k]

    return d


def _build_manifest(
    skill: SkillDefinition,
    skill_yaml_hash: str,
    context_file_paths: list[str],
    exported_by: str = "sidera",
) -> SkillManifest:
    """Build a manifest for a skill bundle."""
    return SkillManifest(
        format_version=BUNDLE_FORMAT_VERSION,
        skill_id=skill.id,
        skill_name=skill.name,
        skill_version=skill.version,
        description=skill.description,
        author=skill.author,
        category=skill.category,
        platforms=list(skill.platforms),
        tags=list(skill.tags),
        exported_at=datetime.now(timezone.utc).isoformat(),
        exported_by=exported_by,
        sha256=skill_yaml_hash,
        context_files=context_file_paths,
        compatibility={
            "min_format_version": "1.0",
            "required_platforms": list(skill.platforms),
            "required_tools": list(skill.tools_required),
            "model_tier": skill.model,
        },
        provenance={
            "original_department": skill.department_id or None,
            "original_role": skill.role_id or None,
        },
    )


def export_skill_to_dir(
    skill: SkillDefinition,
    output_dir: str | Path,
    exported_by: str = "sidera",
) -> Path:
    """Export a skill as a portable directory bundle.

    Creates a directory at ``output_dir/<skill_id>/`` containing:
    - ``manifest.yaml``
    - ``skill.yaml``
    - ``context/`` (if the skill has context files)

    Args:
        skill: The skill definition to export.
        output_dir: Parent directory for the bundle.
        exported_by: Attribution for who exported.

    Returns:
        Path to the created bundle directory.
    """
    output_dir = Path(output_dir)
    bundle_dir = output_dir / skill.id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export skill YAML
    portable = _skill_to_portable_dict(skill)
    skill_yaml = yaml.dump(
        portable,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    skill_yaml_path = bundle_dir / "skill.yaml"
    skill_yaml_path.write_text(skill_yaml, encoding="utf-8")

    # 2. Copy context files
    context_paths: list[str] = []
    if skill.source_dir and skill.context_files:
        src_dir = Path(skill.source_dir)
        ctx_dir = bundle_dir / "context"

        for pattern in skill.context_files:
            for src_file in sorted(src_dir.glob(pattern)):
                if src_file.is_file():
                    # Preserve relative path under context/
                    rel = src_file.relative_to(src_dir)
                    dst = ctx_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst)
                    context_paths.append(str(rel))

    # 3. Build and write manifest
    sha256 = hashlib.sha256(skill_yaml.encode()).hexdigest()
    manifest = _build_manifest(skill, sha256, context_paths, exported_by)
    manifest_yaml = yaml.dump(
        asdict(manifest),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    (bundle_dir / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")

    return bundle_dir


def export_skill_to_zip(
    skill: SkillDefinition,
    output_path: str | Path,
    exported_by: str = "sidera",
) -> Path:
    """Export a skill as a portable ZIP bundle.

    Creates a ZIP file at ``output_path`` containing the same structure
    as ``export_skill_to_dir``.

    Args:
        skill: The skill definition to export.
        output_path: Path for the output ZIP file.
        exported_by: Attribution for who exported.

    Returns:
        Path to the created ZIP file.
    """
    output_path = Path(output_path)

    # Export to a temp dir first, then zip
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = export_skill_to_dir(skill, tmp, exported_by)
        # Create ZIP
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(bundle_dir.parent)
                    zf.write(file_path, arcname)

    return output_path


def export_skill_to_bytes(
    skill: SkillDefinition,
    exported_by: str = "sidera",
) -> bytes:
    """Export a skill as a ZIP bundle in memory.

    Returns:
        Raw bytes of the ZIP file.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = export_skill_to_dir(skill, tmp, exported_by)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(bundle_dir.parent)
                    zf.write(file_path, arcname)
        return buf.getvalue()


# =============================================================================
# Import
# =============================================================================


def _load_manifest(bundle_dir: Path) -> SkillManifest | None:
    """Load and parse the manifest from a bundle directory."""
    manifest_path = bundle_dir / "manifest.yaml"
    if not manifest_path.exists():
        return None

    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        fields = SkillManifest.__dataclass_fields__
        filtered = {k: v for k, v in data.items() if k in fields}
        return SkillManifest(**filtered)
    except Exception:
        return None


def _load_skill_from_bundle(bundle_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Load skill YAML from a bundle directory.

    Returns:
        Tuple of (skill dict, list of errors).
    """
    skill_path = bundle_dir / "skill.yaml"
    if not skill_path.exists():
        return None, ["No skill.yaml found in bundle"]

    try:
        data = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"Invalid YAML: {exc}"]

    if not isinstance(data, dict):
        return None, ["skill.yaml must contain a YAML mapping"]

    return data, []


def _verify_integrity(bundle_dir: Path, manifest: SkillManifest) -> list[str]:
    """Verify the SHA-256 hash of skill.yaml matches the manifest."""
    if not manifest.sha256:
        return []  # No hash to verify

    skill_path = bundle_dir / "skill.yaml"
    if not skill_path.exists():
        return ["Cannot verify integrity: skill.yaml missing"]

    actual = hashlib.sha256(skill_path.read_bytes()).hexdigest()

    if actual != manifest.sha256:
        return [
            f"Integrity check failed: "
            f"expected SHA-256 {manifest.sha256[:16]}..., "
            f"got {actual[:16]}..."
        ]

    return []


def validate_bundle(bundle_path: str | Path) -> ImportResult:
    """Validate a skill bundle without importing it.

    Auto-detects the bundle format (Sidera or Anthropic) and validates
    accordingly.

    Checks:
    - Bundle structure (manifest.yaml, skill.yaml or SKILL.md)
    - Manifest integrity (SHA-256 hash)
    - Skill definition validation (categories, models, etc.)
    - Context file references

    Args:
        bundle_path: Path to the bundle directory or ZIP file.

    Returns:
        An ``ImportResult`` with validation errors/warnings.
    """
    from src.skills.anthropic_compat import is_anthropic_bundle

    bundle_path = Path(bundle_path)

    # Auto-detect Anthropic format
    if is_anthropic_bundle(bundle_path):
        return _validate_anthropic_bundle(bundle_path)

    result = ImportResult()

    # Handle ZIP files
    if bundle_path.suffix == ".zip":
        return _validate_zip_bundle(bundle_path)

    if not bundle_path.is_dir():
        result.errors.append(f"Bundle path is not a directory: {bundle_path}")
        return result

    # Load manifest
    manifest = _load_manifest(bundle_path)
    if manifest is None:
        result.warnings.append("No manifest.yaml found (optional but recommended)")
    else:
        # Verify integrity
        integrity_errors = _verify_integrity(bundle_path, manifest)
        result.errors.extend(integrity_errors)
        result.skill_id = manifest.skill_id
        result.skill_name = manifest.skill_name

    # Load skill definition
    skill_data, load_errors = _load_skill_from_bundle(bundle_path)
    if load_errors:
        result.errors.extend(load_errors)
        return result

    assert skill_data is not None

    # Check required fields
    required = {
        "id",
        "name",
        "version",
        "description",
        "category",
        "model",
        "system_supplement",
        "prompt_template",
        "output_format",
        "business_guidance",
    }
    missing = required - set(skill_data.keys())
    if missing:
        result.errors.append(f"Missing required fields: {', '.join(sorted(missing))}")
        return result

    # Build a temporary SkillDefinition for validation.
    # Strip context_files for schema validation — validate_skill() would
    # try to resolve them against source_dir (the bundle path) which won't
    # match.  We check context files separately below.
    validation_data = {k: v for k, v in skill_data.items() if k != "context_files"}
    try:
        skill_for_validation = _dict_to_skill(validation_data, bundle_path)
    except Exception as exc:
        result.errors.append(f"Cannot construct skill: {exc}")
        return result

    # Run schema validation (without context_files check)
    validation_errors = validate_skill(skill_for_validation)
    result.errors.extend(validation_errors)

    # Build the full skill (with context_files) for metadata extraction
    try:
        skill = _dict_to_skill(skill_data, bundle_path)
    except Exception as exc:
        result.errors.append(f"Cannot construct skill: {exc}")
        return result

    result.skill_id = skill.id
    result.skill_name = skill.name

    # Check context file references
    if skill.context_files:
        ctx_dir = bundle_path / "context"
        if not ctx_dir.exists():
            result.warnings.append("Skill references context_files but no context/ dir in bundle")
        else:
            for pattern in skill.context_files:
                matches = list(ctx_dir.glob(pattern))
                if not matches:
                    result.warnings.append(f"context_files pattern '{pattern}' has no matches")

    # Warn about cross-skill references that may not exist in target
    if skill.references:
        ref_ids = [r[0] for r in skill.references if r[0]]
        if ref_ids:
            result.warnings.append(
                f"Skill references {len(ref_ids)} other skill(s): "
                f"{', '.join(ref_ids)}. Verify these exist in the target "
                f"environment."
            )

    result.success = len(result.errors) == 0
    return result


def _validate_zip_bundle(zip_path: Path) -> ImportResult:
    """Validate a ZIP-packaged skill bundle."""
    import tempfile

    result = ImportResult()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp)
            # Find the bundle root (may be nested one level)
            tmp_path = Path(tmp)
            subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
            if subdirs and (subdirs[0] / "skill.yaml").exists():
                return validate_bundle(subdirs[0])
            elif (tmp_path / "skill.yaml").exists():
                return validate_bundle(tmp_path)
            else:
                result.errors.append("ZIP does not contain skill.yaml")
    except zipfile.BadZipFile:
        result.errors.append("Invalid ZIP file")
    except Exception as exc:
        result.errors.append(f"Error reading ZIP: {exc}")

    return result


def _dict_to_skill(
    data: dict[str, Any],
    bundle_dir: Path | None = None,
) -> SkillDefinition:
    """Convert a portable skill dict back to a SkillDefinition."""
    # Handle context_file_descriptions from list-of-dicts format
    cfd = data.get("context_file_descriptions", [])
    if cfd and isinstance(cfd[0], dict):
        cfd = tuple(
            (d.get("pattern", ""), d.get("description", ""))
            for d in cfd
            if isinstance(d, dict) and d.get("pattern")
        )
    elif cfd and isinstance(cfd[0], (list, tuple)):
        cfd = tuple(tuple(pair) for pair in cfd)
    else:
        cfd = ()

    # Handle references from list-of-dicts format
    raw_refs = data.get("references", [])
    if raw_refs and isinstance(raw_refs[0], dict):
        refs = tuple(
            (
                str(r.get("skill_id", "")),
                str(r.get("relationship", "")),
                str(r.get("reason", "")),
            )
            for r in raw_refs
            if isinstance(r, dict) and r.get("skill_id")
        )
    elif raw_refs and isinstance(raw_refs[0], (list, tuple)):
        refs = tuple(tuple(str(x) for x in ref) for ref in raw_refs)
    else:
        refs = ()

    return SkillDefinition(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data.get("version", "1.0")),
        description=str(data["description"]),
        category=str(data["category"]),
        platforms=tuple(str(p) for p in data.get("platforms", [])),
        tags=tuple(str(t) for t in data.get("tags", [])),
        tools_required=tuple(str(t) for t in data.get("tools_required", [])),
        model=str(data.get("model", "sonnet")),
        max_turns=int(data.get("max_turns", 20)),
        system_supplement=str(data.get("system_supplement", "")),
        prompt_template=str(data.get("prompt_template", "")),
        output_format=str(data.get("output_format", "")),
        business_guidance=str(data.get("business_guidance", "")),
        context_files=tuple(str(cf) for cf in data.get("context_files", [])),
        context_file_descriptions=cfd,
        references=refs,
        source_dir=str(bundle_dir) if bundle_dir else "",
        schedule=data.get("schedule"),
        chain_after=data.get("chain_after"),
        requires_approval=bool(data.get("requires_approval", True)),
        min_clearance=str(data.get("min_clearance", "public")),
        author=str(data.get("author", "imported")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def import_skill_from_bundle(
    bundle_path: str | Path,
    target_department_id: str = "",
    target_role_id: str = "",
    *,
    new_skill_id: str = "",
    new_author: str = "",
    install_to_disk: str | Path | None = None,
) -> ImportResult:
    """Import a skill from a portable bundle.

    Auto-detects the bundle format (Sidera or Anthropic) and imports
    accordingly.

    Validates the bundle, then either:
    - Installs to disk (if ``install_to_disk`` is provided)
    - Returns the validated skill data for DB installation

    Args:
        bundle_path: Path to the bundle directory or ZIP file.
        target_department_id: Department to install into.
        target_role_id: Role to install into.
        new_skill_id: Override the skill ID (for forking).
        new_author: Override the author attribution.
        install_to_disk: If provided, copy skill files to this directory.

    Returns:
        An ``ImportResult`` with success status and details.
    """
    from src.skills.anthropic_compat import is_anthropic_bundle

    bundle_path = Path(bundle_path)

    # Auto-detect Anthropic format
    if is_anthropic_bundle(bundle_path):
        from src.skills.anthropic_compat import import_anthropic_skill

        return import_anthropic_skill(
            source=bundle_path,
            target_dir=install_to_disk,
            target_department_id=target_department_id,
            target_role_id=target_role_id,
            new_skill_id=new_skill_id,
            new_author=new_author,
        )

    result = ImportResult(
        target_department_id=target_department_id,
        target_role_id=target_role_id,
    )

    # Handle ZIP files by extracting first
    extracted_dir = None
    actual_bundle_dir = bundle_path

    if bundle_path.suffix == ".zip":
        import tempfile

        extracted_dir = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(bundle_path, "r") as zf:
                zf.extractall(extracted_dir)
            # Find bundle root
            tmp_path = Path(extracted_dir)
            subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
            if subdirs and (subdirs[0] / "skill.yaml").exists():
                actual_bundle_dir = subdirs[0]
            elif (tmp_path / "skill.yaml").exists():
                actual_bundle_dir = tmp_path
            else:
                result.errors.append("ZIP does not contain skill.yaml")
                return result
        except Exception as exc:
            result.errors.append(f"Cannot extract ZIP: {exc}")
            return result

    try:
        return _do_import(
            actual_bundle_dir,
            target_department_id,
            target_role_id,
            new_skill_id,
            new_author,
            install_to_disk,
        )
    finally:
        if extracted_dir:
            shutil.rmtree(extracted_dir, ignore_errors=True)


def _do_import(
    bundle_dir: Path,
    target_department_id: str,
    target_role_id: str,
    new_skill_id: str,
    new_author: str,
    install_to_disk: str | Path | None,
) -> ImportResult:
    """Internal import logic."""
    result = ImportResult(
        target_department_id=target_department_id,
        target_role_id=target_role_id,
    )

    # Validate first
    validation = validate_bundle(bundle_dir)
    if not validation.success:
        result.errors = validation.errors
        result.warnings = validation.warnings
        return result

    result.warnings = validation.warnings

    # Load skill data
    skill_data, _ = _load_skill_from_bundle(bundle_dir)
    if skill_data is None:
        result.errors.append("Failed to load skill data")
        return result

    # Apply overrides
    if new_skill_id:
        skill_data["id"] = new_skill_id
    if new_author:
        skill_data["author"] = new_author

    result.skill_id = skill_data["id"]
    result.skill_name = skill_data.get("name", "")

    # Install to disk if requested
    if install_to_disk:
        install_path = Path(install_to_disk)
        target_dir = install_path / skill_data["id"]
        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy skill.yaml
        skill_yaml = yaml.dump(
            skill_data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        (target_dir / "skill.yaml").write_text(skill_yaml, encoding="utf-8")

        # Copy context files
        ctx_count = 0
        ctx_src = bundle_dir / "context"
        if ctx_src.exists():
            ctx_dst = target_dir / "context"
            if ctx_dst.exists():
                shutil.rmtree(ctx_dst)
            shutil.copytree(ctx_src, ctx_dst)
            ctx_count = sum(1 for f in ctx_dst.rglob("*") if f.is_file())

        result.context_files_count = ctx_count

    result.success = True
    return result


# =============================================================================
# Listing / search helpers
# =============================================================================


def list_bundles_in_dir(
    directory: str | Path,
) -> list[SkillManifest]:
    """List all skill bundles found in a directory.

    Scans for subdirectories containing ``manifest.yaml`` or ``skill.yaml``.

    Args:
        directory: Directory to scan.

    Returns:
        List of loaded manifests (or synthetic manifests for bundles
        without a manifest.yaml).
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    manifests: list[SkillManifest] = []

    for subdir in sorted(directory.iterdir()):
        if not subdir.is_dir():
            continue

        # Try manifest first
        manifest = _load_manifest(subdir)
        if manifest is not None:
            manifests.append(manifest)
            continue

        # Try skill.yaml directly (bundle without manifest)
        skill_data, errors = _load_skill_from_bundle(subdir)
        if skill_data and not errors:
            manifests.append(
                SkillManifest(
                    skill_id=str(skill_data.get("id", subdir.name)),
                    skill_name=str(skill_data.get("name", "")),
                    skill_version=str(skill_data.get("version", "1.0")),
                    description=str(skill_data.get("description", "")),
                    author=str(skill_data.get("author", "unknown")),
                    category=str(skill_data.get("category", "")),
                    platforms=list(skill_data.get("platforms", [])),
                    tags=list(skill_data.get("tags", [])),
                )
            )

    return manifests


def search_bundles(
    bundles: list[SkillManifest],
    *,
    query: str = "",
    category: str = "",
    platform: str = "",
) -> list[SkillManifest]:
    """Filter a list of skill manifests by search criteria.

    Args:
        bundles: List of manifests to search.
        query: Free-text search (matches ID, name, description, tags).
        category: Filter by category.
        platform: Filter by required platform.

    Returns:
        Filtered list of matching manifests.
    """
    results = bundles

    if category:
        results = [m for m in results if m.category == category]

    if platform:
        results = [m for m in results if platform in m.platforms]

    if query:
        q = query.lower()
        results = [
            m
            for m in results
            if q in m.skill_id.lower()
            or q in m.skill_name.lower()
            or q in m.description.lower()
            or any(q in t.lower() for t in m.tags)
        ]

    return results


# =============================================================================
# Anthropic format helpers (delegated to anthropic_compat module)
# =============================================================================


def _validate_anthropic_bundle(bundle_path: Path) -> ImportResult:
    """Validate an Anthropic-format skill bundle."""
    from src.skills.anthropic_compat import validate_anthropic_bundle

    result = ImportResult()
    is_valid, errors, warnings = validate_anthropic_bundle(bundle_path)
    result.errors.extend(errors)
    result.warnings.extend(warnings)
    result.success = is_valid

    if is_valid:
        from src.skills.anthropic_compat import parse_skill_md

        try:
            bundle = parse_skill_md(bundle_path)
            result.skill_id = bundle.name.replace("-", "_")
            result.skill_name = bundle.name.replace("-", " ").title()
        except Exception:
            pass

    return result


def export_skill_to_anthropic(
    skill: SkillDefinition,
    output_dir: str | Path,
    exported_by: str = "sidera",
) -> Path:
    """Export a Sidera skill as an Anthropic-compatible SKILL.md bundle.

    Args:
        skill: The skill to export.
        output_dir: Parent directory for the output.
        exported_by: Attribution for the export.

    Returns:
        Path to the created Anthropic skill directory.
    """
    from src.skills.anthropic_compat import export_to_anthropic_dir

    skill_dict = asdict(skill)
    return export_to_anthropic_dir(
        skill_dict,
        output_dir,
        source_skill_dir=skill.source_dir or None,
    )
