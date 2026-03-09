"""Skill, role, and department registry for Sidera.

Loads all YAML definitions from disk using a three-level hierarchy
and provides lookup, filtering, and search capabilities.

Hierarchy on disk::

    library/
      marketing/                         ← Department
        _department.yaml
        performance_media_buyer/         ← Role
          _role.yaml
          creative_analysis/             ← Skill (folder-based)
            skill.yaml
          budget_reallocation.yaml       ← Skill (flat)
      standalone_skill.yaml              ← Loose skill (backward compat)

Discovery order:

1. Scan each subdirectory for ``_department.yaml`` → ``DepartmentDefinition``
2. Inside each department, scan subdirectories for ``_role.yaml`` → ``RoleDefinition``
3. Inside each role, load skills (flat ``.yaml`` + folder ``skill.yaml``)
4. Load loose skills directly in ``library/`` (no dept/role)

Usage::

    from src.skills.registry import SkillRegistry

    registry = SkillRegistry()
    count = registry.load_all()

    dept = registry.get_department("marketing")
    role = registry.get_role("performance_media_buyer")
    skill = registry.get("creative_analysis")
    skills = registry.list_skills_for_role("performance_media_buyer")
"""

from __future__ import annotations

from pathlib import Path

import structlog

from src.skills.auto_execute import AutoExecuteRuleSet, load_rules_from_yaml
from src.skills.schema import (
    DepartmentDefinition,
    RoleDefinition,
    SkillDefinition,
    SkillLoadError,
    load_department_from_yaml,
    load_role_from_yaml,
    load_skill_from_yaml,
    validate_department,
    validate_role,
    validate_skill,
)

logger = structlog.get_logger(__name__)

# Default skills directory relative to this file
_DEFAULT_SKILLS_DIR = Path(__file__).parent / "library"

# Sentinel filenames for hierarchy config
_DEPARTMENT_FILE = "_department.yaml"
_DEPARTMENT_FILE_ALT = "_department.yml"
_ROLE_FILE = "_role.yaml"
_ROLE_FILE_ALT = "_role.yml"
_RULES_FILE = "_rules.yaml"
_RULES_FILE_ALT = "_rules.yml"


class SkillRegistry:
    """In-memory registry of loaded skill, role, and department definitions.

    Loads YAML files from disk, validates them, and provides fast lookup
    by ID, category, platform, and keyword search. Supports both the
    three-level hierarchy (department → role → skill) and loose skills
    for backward compatibility.

    Args:
        skills_dir: Path to the directory containing skill definitions.
            Defaults to ``src/skills/library/``.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._skills: dict[str, SkillDefinition] = {}
        self._departments: dict[str, DepartmentDefinition] = {}
        self._roles: dict[str, RoleDefinition] = {}
        self._rulesets: dict[str, AutoExecuteRuleSet] = {}
        self._sources: dict[str, str] = {}  # "dept:X" / "role:X" / "skill:X" -> "disk" | "db"
        # referenced_skill_id -> set of referencing skill_ids
        self._reverse_references: dict[str, set[str]] = {}
        self._log = logger.bind(component="skill_registry")

    # ------------------------------------------------------------------
    # Direct registration (for plugins and programmatic use)
    # ------------------------------------------------------------------

    def register_skill(self, skill: SkillDefinition) -> None:
        """Register a skill definition directly (not from disk).

        Useful for loading skills from plugins or tests.  If a skill
        with the same ID already exists it will be overwritten.
        """
        if skill.id in self._skills:
            self._log.debug("skill_registry.overwrite", skill_id=skill.id)
        self._skills[skill.id] = skill
        self._sources[f"skill:{skill.id}"] = "plugin"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self) -> int:
        """Load all definitions from the skills directory.

        Discovery follows the three-level hierarchy:

        1. Subdirectories with ``_department.yaml`` → departments
        2. Inside each department, subdirs with ``_role.yaml`` → roles
        3. Inside each role, ``.yaml`` files + ``skill.yaml`` folders → skills
        4. Loose ``.yaml`` files and ``skill.yaml`` folders at the top level

        Clears all previously loaded definitions before loading.

        Returns:
            Number of skills successfully loaded.
        """
        self._skills.clear()
        self._departments.clear()
        self._roles.clear()
        self._rulesets.clear()
        self._sources.clear()

        if not self._skills_dir.exists():
            self._log.warning(
                "skills_dir.not_found",
                path=str(self._skills_dir),
            )
            return 0

        if not self._skills_dir.is_dir():
            self._log.warning(
                "skills_dir.not_directory",
                path=str(self._skills_dir),
            )
            return 0

        # Phase 1: Scan for departments, roles, and their skills
        for child in sorted(self._skills_dir.iterdir()):
            if not child.is_dir():
                continue

            dept_file = self._find_department_file(child)
            if dept_file is not None:
                dept = self._load_department(dept_file)
                if dept is not None:
                    self._scan_department_dir(child, dept.id)
                continue

            # Not a department — could be a loose folder-based skill
            self._try_load_folder_skill(child)

        # Phase 2: Loose flat skill files at the top level
        for yaml_path in sorted(
            list(self._skills_dir.glob("*.yaml")) + list(self._skills_dir.glob("*.yml"))
        ):
            # Skip _department.yaml/_role.yaml at top level (shouldn't exist)
            if yaml_path.name.startswith("_"):
                continue
            self._load_single_skill(yaml_path)

        # Phase 3: Cross-validate manager → managed role references
        self._validate_manager_references()

        # Phase 4: Build cross-skill reference index
        self._build_reverse_references()

        self._log.info(
            "registry.loaded",
            departments=len(self._departments),
            roles=len(self._roles),
            skills=len(self._skills),
            managers=sum(1 for r in self._roles.values() if r.manages),
            directory=str(self._skills_dir),
        )
        return len(self._skills)

    def _find_department_file(self, directory: Path) -> Path | None:
        """Check if a directory contains a department config file."""
        for name in (_DEPARTMENT_FILE, _DEPARTMENT_FILE_ALT):
            candidate = directory / name
            if candidate.exists():
                return candidate
        return None

    def _find_role_file(self, directory: Path) -> Path | None:
        """Check if a directory contains a role config file."""
        for name in (_ROLE_FILE, _ROLE_FILE_ALT):
            candidate = directory / name
            if candidate.exists():
                return candidate
        return None

    def _load_department(
        self,
        yaml_path: Path,
    ) -> DepartmentDefinition | None:
        """Load and validate a department YAML file."""
        try:
            dept = load_department_from_yaml(yaml_path)
            errors = validate_department(dept)
            if errors:
                self._log.warning(
                    "department.validation_failed",
                    path=str(yaml_path),
                    dept_id=dept.id,
                    errors=errors,
                )
                return None

            if dept.id in self._departments:
                self._log.warning(
                    "department.duplicate_id",
                    dept_id=dept.id,
                    new_path=str(yaml_path),
                )
                return None

            self._departments[dept.id] = dept
            self._sources[f"dept:{dept.id}"] = "disk"
            self._log.debug(
                "department.loaded",
                dept_id=dept.id,
                name=dept.name,
            )
            return dept

        except SkillLoadError as exc:
            self._log.warning(
                "department.load_failed",
                path=str(yaml_path),
                error=str(exc),
            )
            return None

    def _scan_department_dir(
        self,
        dept_dir: Path,
        department_id: str,
    ) -> None:
        """Scan a department directory for roles and loose skills."""
        for child in sorted(dept_dir.iterdir()):
            if not child.is_dir():
                continue

            # Skip if it's a context/examples dir for the department
            if child.name.startswith("_") or child.name in (
                "context",
                "examples",
                "guidelines",
            ):
                continue

            role_file = self._find_role_file(child)
            if role_file is not None:
                role = self._load_role(role_file)
                if role is not None:
                    self._scan_role_dir(child, department_id, role.id)
                continue

            # Not a role — could be a folder-based skill inside the dept
            self._try_load_folder_skill(
                child,
                department_id=department_id,
            )

        # Flat skill files directly in the department dir
        for yaml_path in sorted(list(dept_dir.glob("*.yaml")) + list(dept_dir.glob("*.yml"))):
            if yaml_path.name.startswith("_"):
                continue
            self._load_single_skill(
                yaml_path,
                department_id=department_id,
            )

    def _load_role(
        self,
        yaml_path: Path,
    ) -> RoleDefinition | None:
        """Load and validate a role YAML file."""
        try:
            role = load_role_from_yaml(yaml_path)
            errors = validate_role(role)
            if errors:
                self._log.warning(
                    "role.validation_failed",
                    path=str(yaml_path),
                    role_id=role.id,
                    errors=errors,
                )
                return None

            if role.id in self._roles:
                self._log.warning(
                    "role.duplicate_id",
                    role_id=role.id,
                    new_path=str(yaml_path),
                )
                return None

            self._roles[role.id] = role
            self._sources[f"role:{role.id}"] = "disk"
            self._log.debug(
                "role.loaded",
                role_id=role.id,
                name=role.name,
                department_id=role.department_id,
            )
            return role

        except SkillLoadError as exc:
            self._log.warning(
                "role.load_failed",
                path=str(yaml_path),
                error=str(exc),
            )
            return None

    def _find_rules_file(self, directory: Path) -> Path | None:
        """Check if a directory contains a rules config file."""
        for name in (_RULES_FILE, _RULES_FILE_ALT):
            candidate = directory / name
            if candidate.exists():
                return candidate
        return None

    def _load_rules(
        self,
        yaml_path: Path,
        role_id: str,
    ) -> None:
        """Load and validate a rules YAML file for a role."""
        try:
            ruleset = load_rules_from_yaml(yaml_path)
            self._rulesets[role_id] = ruleset
            self._log.debug(
                "rules.loaded",
                role_id=role_id,
                rule_count=len(ruleset.rules),
                path=str(yaml_path),
            )
        except Exception as exc:
            self._log.warning(
                "rules.load_failed",
                role_id=role_id,
                path=str(yaml_path),
                error=str(exc),
            )

    def _scan_role_dir(
        self,
        role_dir: Path,
        department_id: str,
        role_id: str,
    ) -> None:
        """Scan a role directory for skills (flat + folder-based)."""
        # Load auto-execute rules if present
        rules_file = self._find_rules_file(role_dir)
        if rules_file is not None:
            self._load_rules(rules_file, role_id)

        # Folder-based skills inside the role dir
        for child in sorted(role_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("_") or child.name in (
                "context",
                "examples",
                "guidelines",
            ):
                continue
            self._try_load_folder_skill(
                child,
                department_id=department_id,
                role_id=role_id,
            )

        # Flat skill files inside the role dir
        for yaml_path in sorted(list(role_dir.glob("*.yaml")) + list(role_dir.glob("*.yml"))):
            if yaml_path.name.startswith("_"):
                continue
            self._load_single_skill(
                yaml_path,
                department_id=department_id,
                role_id=role_id,
            )

    def _try_load_folder_skill(
        self,
        folder: Path,
        department_id: str = "",
        role_id: str = "",
    ) -> None:
        """Try to load a folder-based skill (contains skill.yaml)."""
        for candidate in ("skill.yaml", "skill.yml"):
            skill_path = folder / candidate
            if skill_path.exists():
                self._load_single_skill(
                    skill_path,
                    department_id=department_id,
                    role_id=role_id,
                )
                return

    def _load_single_skill(
        self,
        yaml_path: Path,
        department_id: str = "",
        role_id: str = "",
    ) -> None:
        """Load and validate a single skill YAML file.

        Args:
            yaml_path: Path to the ``.yaml`` file.
            department_id: Department this skill belongs to (empty if loose).
            role_id: Role this skill belongs to (empty if loose).
        """
        try:
            skill = load_skill_from_yaml(yaml_path)

            # Wire hierarchy fields based on disk location
            if department_id or role_id:
                # Create a new instance with hierarchy fields set
                # (SkillDefinition is frozen, so we rebuild)
                skill = SkillDefinition(
                    **{
                        **{
                            f.name: getattr(skill, f.name)
                            for f in skill.__dataclass_fields__.values()
                        },
                        "department_id": department_id,
                        "role_id": role_id,
                    }
                )

            # Validate
            errors = validate_skill(skill)
            if errors:
                self._log.warning(
                    "skill.validation_failed",
                    path=str(yaml_path),
                    skill_id=skill.id,
                    errors=errors,
                )
                return

            # Check for duplicate IDs
            if skill.id in self._skills:
                self._log.warning(
                    "skill.duplicate_id",
                    skill_id=skill.id,
                    existing_path="(already loaded)",
                    new_path=str(yaml_path),
                )
                return

            self._skills[skill.id] = skill
            self._sources[f"skill:{skill.id}"] = "disk"
            self._log.debug(
                "skill.loaded",
                skill_id=skill.id,
                name=skill.name,
                department_id=department_id,
                role_id=role_id,
                folder_based=bool(skill.context_files),
            )

        except SkillLoadError as exc:
            self._log.warning(
                "skill.load_failed",
                path=str(yaml_path),
                error=str(exc),
            )

    def _validate_manager_references(self) -> None:
        """Warn about manager roles that reference non-existent managed roles.

        Also detects circular management chains (A→B→A) with a depth
        limit of 3 to prevent infinite recursion.
        """
        for role_id, role in self._roles.items():
            if not role.manages:
                continue

            for managed_id in role.manages:
                if managed_id not in self._roles:
                    self._log.warning(
                        "manager.managed_role_not_found",
                        manager_id=role_id,
                        managed_id=managed_id,
                    )
                elif managed_id == role_id:
                    self._log.warning(
                        "manager.self_reference",
                        manager_id=role_id,
                    )

            # Detect circular chains up to depth 3
            self._check_circular(role_id, depth=0, visited=set())

    def _check_circular(
        self,
        role_id: str,
        depth: int,
        visited: set[str],
    ) -> None:
        """Recursive circular management detection with depth limit."""
        if depth > 3:
            self._log.warning(
                "manager.depth_exceeded",
                role_id=role_id,
                depth=depth,
            )
            return

        if role_id in visited:
            self._log.warning(
                "manager.circular_reference",
                role_id=role_id,
                chain=list(visited),
            )
            return

        role = self._roles.get(role_id)
        if role is None or not role.manages:
            return

        visited_next = visited | {role_id}
        for managed_id in role.manages:
            managed = self._roles.get(managed_id)
            if managed is not None and managed.manages:
                self._check_circular(managed_id, depth + 1, visited_next)

    def _build_reverse_references(self) -> None:
        """Build reverse index: which skills reference which other skills.

        Also warns about dangling references (referenced skill_id not in
        registry). Similar to ``_validate_manager_references()`` for
        manager→managed role cross-references.
        """
        self._reverse_references.clear()

        for skill_id, skill in self._skills.items():
            if not skill.references:
                continue
            for ref_skill_id, relationship, _reason in skill.references:
                if ref_skill_id not in self._skills:
                    self._log.warning(
                        "skill.dangling_reference",
                        skill_id=skill_id,
                        referenced_skill_id=ref_skill_id,
                        relationship=relationship,
                    )
                    continue
                if ref_skill_id not in self._reverse_references:
                    self._reverse_references[ref_skill_id] = set()
                self._reverse_references[ref_skill_id].add(skill_id)

        ref_count = sum(len(v) for v in self._reverse_references.values())
        if ref_count:
            self._log.info(
                "registry.reverse_references_built",
                referenced_skills=len(self._reverse_references),
                total_reference_edges=ref_count,
            )

    def get_references_for(
        self,
        skill_id: str,
    ) -> list[tuple["SkillDefinition", str, str]]:
        """Get the skills that a skill references, with relationship metadata.

        Args:
            skill_id: The skill whose references to look up.

        Returns:
            List of ``(referenced_skill_def, relationship, reason)`` tuples.
            Only includes references whose target exists in the registry.
        """
        skill = self._skills.get(skill_id)
        if skill is None or not skill.references:
            return []

        result: list[tuple[SkillDefinition, str, str]] = []
        for ref_skill_id, relationship, reason in skill.references:
            ref_skill = self._skills.get(ref_skill_id)
            if ref_skill is not None:
                result.append((ref_skill, relationship, reason))
        return result

    def get_referenced_by(self, skill_id: str) -> set[str]:
        """Get skill IDs that reference a given skill (reverse index).

        Args:
            skill_id: The skill to check.

        Returns:
            Set of skill IDs that declare a reference to this skill.
        """
        return self._reverse_references.get(skill_id, set()).copy()

    # ------------------------------------------------------------------
    # DB Merge
    # ------------------------------------------------------------------

    def merge_db_definitions(
        self,
        db_departments: list[dict],
        db_roles: list[dict],
        db_skills: list[dict],
    ) -> None:
        """Overlay DB definitions onto disk-loaded registry.

        DB entries with the same ID as disk entries **replace** them
        entirely.  New IDs are **added**.  Each entry is validated using
        the same validators as YAML; invalid entries are logged and
        skipped.

        After merging, ``_validate_manager_references()`` is re-run to
        catch cross-source issues.

        Args:
            db_departments: List of department dicts from the DB.
            db_roles: List of role dicts from the DB.
            db_skills: List of skill dicts from the DB.
        """
        merged_depts = 0
        merged_roles = 0
        merged_skills = 0

        # --- Departments ---
        for d in db_departments:
            try:
                # Parse vocabulary from DB JSON column
                raw_vocab = d.get("vocabulary") or []
                if isinstance(raw_vocab, list):
                    vocabulary = tuple(
                        (str(v.get("term", "")), str(v.get("definition", "")))
                        for v in raw_vocab
                        if isinstance(v, dict) and v.get("term")
                    )
                else:
                    vocabulary = ()
                dept = DepartmentDefinition(
                    id=str(d.get("dept_id", d.get("id", ""))),
                    name=str(d.get("name", "")),
                    description=str(d.get("description", "")),
                    context=str(d.get("context", "")),
                    context_files=tuple(str(cf) for cf in (d.get("context_files") or [])),
                    source_dir=str(d.get("source_dir", "")),
                    context_text=str(d.get("context_text", "")),
                    vocabulary=vocabulary,
                    routing_keywords=tuple(str(k) for k in (d.get("routing_keywords") or [])),
                    steward=str(d.get("steward_user_id", d.get("steward", "")) or ""),
                    slack_channel_id=str(d.get("slack_channel_id", "") or ""),
                    credentials_scope=str(d.get("credentials_scope", "") or ""),
                )
                errors = validate_department(dept)
                if errors:
                    self._log.warning(
                        "db_merge.department.validation_failed",
                        dept_id=dept.id,
                        errors=errors,
                    )
                    continue
                self._departments[dept.id] = dept
                self._sources[f"dept:{dept.id}"] = "db"
                merged_depts += 1
            except Exception as exc:
                self._log.warning(
                    "db_merge.department.error",
                    data=d,
                    error=str(exc),
                )

        # --- Roles ---
        for r in db_roles:
            try:
                role = RoleDefinition(
                    id=str(r.get("role_id", r.get("id", ""))),
                    name=str(r.get("name", "")),
                    department_id=str(r.get("department_id", "")),
                    description=str(r.get("description", "")),
                    persona=str(r.get("persona", "")),
                    connectors=tuple(str(c) for c in (r.get("connectors") or [])),
                    briefing_skills=tuple(str(s) for s in (r.get("briefing_skills") or [])),
                    schedule=r.get("schedule"),
                    context_files=tuple(str(cf) for cf in (r.get("context_files") or [])),
                    source_dir=str(r.get("source_dir", "")),
                    context_text=str(r.get("context_text", "")),
                    principles=tuple(str(p) for p in (r.get("principles") or [])),
                    goals=tuple(str(g) for g in (r.get("goals") or [])),
                    manages=tuple(str(m) for m in (r.get("manages") or [])),
                    delegation_model=str(r.get("delegation_model", "standard")),
                    synthesis_prompt=str(r.get("synthesis_prompt", "")),
                    clearance_level=str(r.get("clearance_level", "internal")),
                    routing_keywords=tuple(str(k) for k in (r.get("routing_keywords") or [])),
                    heartbeat_schedule=r.get("heartbeat_schedule"),
                    heartbeat_model=str(r.get("heartbeat_model", "")),
                    steward=str(r.get("steward_user_id", r.get("steward", "")) or ""),
                    learning_channels=tuple(str(lc) for lc in (r.get("learning_channels") or [])),
                    document_sync=tuple(
                        (str(d.get("type", "")), str(d.get("doc_id", "")))
                        for d in (r.get("document_sync") or [])
                        if isinstance(d, dict) and d.get("type") and d.get("doc_id")
                    ),
                    event_subscriptions=tuple(str(e) for e in (r.get("event_subscriptions") or [])),
                )
                errors = validate_role(role)
                if errors:
                    self._log.warning(
                        "db_merge.role.validation_failed",
                        role_id=role.id,
                        errors=errors,
                    )
                    continue
                self._roles[role.id] = role
                self._sources[f"role:{role.id}"] = "db"
                merged_roles += 1
            except Exception as exc:
                self._log.warning(
                    "db_merge.role.error",
                    data=r,
                    error=str(exc),
                )

        # --- Skills ---
        for s in db_skills:
            try:
                skill = SkillDefinition(
                    id=str(s.get("skill_id", s.get("id", ""))),
                    name=str(s.get("name", "")),
                    version=str(s.get("version", "1.0")),
                    description=str(s.get("description", "")),
                    category=str(s.get("category", "")),
                    platforms=tuple(str(p) for p in (s.get("platforms") or [])),
                    tags=tuple(str(t) for t in (s.get("tags") or [])),
                    tools_required=tuple(str(t) for t in (s.get("tools_required") or [])),
                    model=str(s.get("model", "sonnet")),
                    max_turns=int(s.get("max_turns", 20)),
                    system_supplement=str(s.get("system_supplement", "")),
                    prompt_template=str(s.get("prompt_template", "")),
                    output_format=str(s.get("output_format", "")),
                    business_guidance=str(s.get("business_guidance", "")),
                    context_files=tuple(str(cf) for cf in (s.get("context_files") or [])),
                    source_dir=str(s.get("source_dir", "")),
                    context_text=str(s.get("context_text", "")),
                    schedule=s.get("schedule"),
                    chain_after=s.get("chain_after"),
                    requires_approval=bool(s.get("requires_approval", True)),
                    min_clearance=str(s.get("min_clearance", "public")),
                    references=tuple(
                        (
                            str(r.get("skill_id", "")),
                            str(r.get("relationship", "")),
                            str(r.get("reason", "")),
                        )
                        for r in (s.get("references") or [])
                        if isinstance(r, dict) and r.get("skill_id")
                    ),
                    department_id=str(s.get("department_id", "")),
                    role_id=str(s.get("role_id", "")),
                    author=str(s.get("author", "sidera")),
                )
                errors = validate_skill(skill)
                if errors:
                    self._log.warning(
                        "db_merge.skill.validation_failed",
                        skill_id=skill.id,
                        errors=errors,
                    )
                    continue
                self._skills[skill.id] = skill
                self._sources[f"skill:{skill.id}"] = "db"
                merged_skills += 1
            except Exception as exc:
                self._log.warning(
                    "db_merge.skill.error",
                    data=s,
                    error=str(exc),
                )

        # Re-validate manager references after merge
        self._validate_manager_references()

        # Rebuild cross-skill reference index
        self._build_reverse_references()

        self._log.info(
            "registry.db_merged",
            departments_merged=merged_depts,
            roles_merged=merged_roles,
            skills_merged=merged_skills,
            total_departments=len(self._departments),
            total_roles=len(self._roles),
            total_skills=len(self._skills),
        )

    def get_source(self, entity_type: str, entity_id: str) -> str:
        """Get the source of an entity ('disk' or 'db').

        Args:
            entity_type: One of 'dept', 'role', 'skill'.
            entity_id: The entity identifier.

        Returns:
            'disk', 'db', or 'unknown'.
        """
        return self._sources.get(f"{entity_type}:{entity_id}", "unknown")

    def reload(self) -> int:
        """Clear all loaded definitions and reload from disk.

        Returns:
            Number of skills successfully loaded.
        """
        self._log.info("registry.reloading")
        return self.load_all()

    # ------------------------------------------------------------------
    # Skill Lookup
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> SkillDefinition | None:
        """Get a skill by its ID.

        Args:
            skill_id: The unique skill identifier.

        Returns:
            The ``SkillDefinition`` if found, otherwise ``None``.
        """
        return self._skills.get(skill_id)

    # Alias for consistency with get_role() / get_department()
    get_skill = get

    def list_all(self) -> list[SkillDefinition]:
        """List all loaded skills, sorted by ID."""
        return sorted(self._skills.values(), key=lambda s: s.id)

    def list_by_category(self, category: str) -> list[SkillDefinition]:
        """List skills filtered by category."""
        return sorted(
            [s for s in self._skills.values() if s.category == category],
            key=lambda s: s.id,
        )

    def list_by_platform(self, platform: str) -> list[SkillDefinition]:
        """List skills that require a specific platform."""
        return sorted(
            [s for s in self._skills.values() if platform in s.platforms],
            key=lambda s: s.id,
        )

    def list_scheduled(self) -> list[SkillDefinition]:
        """List skills that have a cron schedule defined."""
        return sorted(
            [s for s in self._skills.values() if s.schedule is not None],
            key=lambda s: s.id,
        )

    # ------------------------------------------------------------------
    # Department Lookup
    # ------------------------------------------------------------------

    def get_department(
        self,
        dept_id: str,
    ) -> DepartmentDefinition | None:
        """Get a department by its ID."""
        return self._departments.get(dept_id)

    def list_departments(self) -> list[DepartmentDefinition]:
        """List all loaded departments, sorted by ID."""
        return sorted(
            self._departments.values(),
            key=lambda d: d.id,
        )

    # ------------------------------------------------------------------
    # Role Lookup
    # ------------------------------------------------------------------

    def get_role(self, role_id: str) -> RoleDefinition | None:
        """Get a role by its ID."""
        return self._roles.get(role_id)

    def list_roles(
        self,
        department_id: str | None = None,
    ) -> list[RoleDefinition]:
        """List roles, optionally filtered by department.

        Args:
            department_id: If provided, only return roles belonging to
                this department. If ``None``, return all roles.

        Returns:
            Sorted list of role definitions.
        """
        roles = self._roles.values()
        if department_id is not None:
            roles = [r for r in roles if r.department_id == department_id]
        return sorted(roles, key=lambda r: r.id)

    # ------------------------------------------------------------------
    # Auto-Execute Rules
    # ------------------------------------------------------------------

    def get_rules(
        self,
        role_id: str,
    ) -> AutoExecuteRuleSet | None:
        """Get the auto-execute rules for a role.

        Args:
            role_id: The role to get rules for.

        Returns:
            The ``AutoExecuteRuleSet`` if found, otherwise ``None``.
        """
        return self._rulesets.get(role_id)

    def list_rulesets(self) -> list[AutoExecuteRuleSet]:
        """List all loaded rule sets, sorted by role ID."""
        return sorted(
            self._rulesets.values(),
            key=lambda r: r.role_id,
        )

    # ------------------------------------------------------------------
    # Manager Queries
    # ------------------------------------------------------------------

    def is_manager(self, role_id: str) -> bool:
        """Check if a role is a manager (has non-empty ``manages`` field).

        Args:
            role_id: The role ID to check.

        Returns:
            ``True`` if the role exists and manages other roles.
        """
        role = self._roles.get(role_id)
        return role is not None and len(role.manages) > 0

    def get_managed_roles(
        self,
        role_id: str,
    ) -> list[RoleDefinition]:
        """Get the roles managed by a manager role.

        Args:
            role_id: The manager role ID.

        Returns:
            List of ``RoleDefinition`` objects for each managed role that
            exists in the registry.  Missing managed roles are silently
            skipped (a warning was already logged during ``load_all``).
        """
        role = self._roles.get(role_id)
        if role is None or not role.manages:
            return []

        result: list[RoleDefinition] = []
        for managed_id in role.manages:
            managed = self._roles.get(managed_id)
            if managed is not None:
                result.append(managed)
        return result

    def list_managers(
        self,
        department_id: str | None = None,
    ) -> list[RoleDefinition]:
        """List all manager roles, optionally filtered by department.

        Args:
            department_id: If provided, only return managers belonging to
                this department.  If ``None``, return all managers.

        Returns:
            Sorted list of manager role definitions.
        """
        managers = [r for r in self._roles.values() if len(r.manages) > 0]
        if department_id is not None:
            managers = [r for r in managers if r.department_id == department_id]
        return sorted(managers, key=lambda r: r.id)

    # ------------------------------------------------------------------
    # Hierarchy Queries
    # ------------------------------------------------------------------

    def list_skills_for_role(
        self,
        role_id: str,
    ) -> list[SkillDefinition]:
        """List all skills belonging to a role, sorted by ID."""
        return sorted(
            [s for s in self._skills.values() if s.role_id == role_id],
            key=lambda s: s.id,
        )

    def list_skills_for_department(
        self,
        dept_id: str,
    ) -> list[SkillDefinition]:
        """List all skills belonging to a department, sorted by ID."""
        return sorted(
            [s for s in self._skills.values() if s.department_id == dept_id],
            key=lambda s: s.id,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def build_routing_index(self) -> str:
        """Build a compact text index for the SkillRouter.

        Returns a multi-line string where each line has the format:
        ``skill_id | description | tag1, tag2, tag3``

        Returns:
            The routing index text. Empty string if no skills loaded.
        """
        if not self._skills:
            return ""

        lines: list[str] = []
        for skill in sorted(
            self._skills.values(),
            key=lambda s: s.id,
        ):
            tags_str = ", ".join(skill.tags)
            lines.append(f"{skill.id} | {skill.description} | {tags_str}")

        return "\n".join(lines)

    def search(self, query: str) -> list[SkillDefinition]:
        """Search skills by keyword matching.

        Performs a case-insensitive search. A skill matches if any
        query word appears in its ID, description, tags, or category.

        Args:
            query: Space-separated search terms.

        Returns:
            List of matching skills, sorted by relevance then ID.
        """
        if not query.strip():
            return []

        query_words = [w.lower() for w in query.split() if w]
        if not query_words:
            return []

        scored: list[tuple[int, SkillDefinition]] = []

        for skill in self._skills.values():
            searchable = " ".join(
                [
                    skill.id,
                    skill.name.lower(),
                    skill.description.lower(),
                    skill.category.lower(),
                    " ".join(skill.tags),
                    " ".join(skill.platforms),
                ]
            ).lower()

            matches = sum(1 for word in query_words if word in searchable)
            if matches > 0:
                scored.append((matches, skill))

        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [skill for _, skill in scored]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of loaded skills."""
        return len(self._skills)

    @property
    def department_count(self) -> int:
        """Number of loaded departments."""
        return len(self._departments)

    @property
    def role_count(self) -> int:
        """Number of loaded roles."""
        return len(self._roles)

    @property
    def ruleset_count(self) -> int:
        """Number of loaded auto-execute rule sets."""
        return len(self._rulesets)

    @property
    def skills_dir(self) -> Path:
        """The directory definitions are loaded from."""
        return self._skills_dir

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def __repr__(self) -> str:
        return (
            f"SkillRegistry(skills_dir={self._skills_dir!r}, "
            f"departments={len(self._departments)}, "
            f"roles={len(self._roles)}, "
            f"skills={len(self._skills)})"
        )
