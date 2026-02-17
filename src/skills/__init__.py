"""Skill system for Sidera.

Provides a registry of YAML-defined skills, a semantic router for matching
user queries to skills, and an executor for running skills through the
SideraAgent.

Submodules:
- schema: SkillDefinition/RoleDefinition/DepartmentDefinition dataclasses + YAML loader
- registry: SkillRegistry for loading, indexing, and searching (dept → role → skill)
- router: SkillRouter for Haiku-based semantic matching
- role_router: RoleRouter for conversation-mode role identification (regex + Haiku)
- executor: SkillExecutor + RoleExecutor + DepartmentExecutor
- manager: ManagerExecutor for hierarchical delegation (own skills → delegate → synthesize)
- memory: Role memory extraction and composition (hot/cold tiered architecture)
- auto_execute: Graduated trust rule engine for auto-approved actions
- db_loader: load_registry_with_db() — standard entry point with DB overlay
- evolution: Skill evolution engine — agents propose changes to their own skills
"""
