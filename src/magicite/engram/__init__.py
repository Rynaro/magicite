"""Public Engram package surface.

0.2 modules remain importable from their historical paths. Engram 1.0
contracts for downstream slices (S03+) are re-exported here for a stable
import root.
"""

from __future__ import annotations

from magicite.engram.assets import (
    AssetValidationError,
    assert_assets_valid,
    resolve_asset_path,
    validate_assets,
)
from magicite.engram.digests import (
    asset_bytes_digest,
    assets_manifest_digest,
    canonical_json_bytes,
    metadata_digest,
    projection_digest,
    routing_body_digest,
)
from magicite.engram.model_v1 import (
    KNOWN_EXTENSIONS,
    AssetDescriptor,
    Capabilities,
    Compatibility,
    EngramFrontmatterV1,
    EngramRevisionRef,
    EngramV1,
    ExtensionValue,
    Origin,
    ProducedCapability,
    Relations,
    RequiredCapability,
    Risk,
    RoutingV1,
    VersionConstraint,
)
from magicite.engram.parser import (
    load_artifact,
    load_artifact_file,
    parse_artifact,
    parse_artifact_file,
)
from magicite.engram.schema_validate import (
    EngramSchemaError,
    assert_valid_frontmatter,
    clear_schema_cache,
    load_schema,
    schema_path,
    validate_frontmatter_dict,
    validate_frontmatter_v1,
)
from magicite.engram.transform import TransformDiagnostic, TransformResult, transform_0_2_to_1_0
from magicite.engram.version_constraints import VersionConstraintError, validate_version_constraint
from magicite.engram.writer import TargetFormat, render_as, render_document_v1, write_engram_as

__all__ = [
    "KNOWN_EXTENSIONS",
    "AssetDescriptor",
    "AssetValidationError",
    "Capabilities",
    "Compatibility",
    "EngramFrontmatterV1",
    "EngramRevisionRef",
    "EngramSchemaError",
    "EngramV1",
    "ExtensionValue",
    "Origin",
    "ProducedCapability",
    "Relations",
    "RequiredCapability",
    "Risk",
    "RoutingV1",
    "TargetFormat",
    "TransformDiagnostic",
    "TransformResult",
    "VersionConstraint",
    "VersionConstraintError",
    "assert_assets_valid",
    "assert_valid_frontmatter",
    "asset_bytes_digest",
    "assets_manifest_digest",
    "canonical_json_bytes",
    "clear_schema_cache",
    "load_artifact",
    "load_artifact_file",
    "load_schema",
    "metadata_digest",
    "parse_artifact",
    "parse_artifact_file",
    "projection_digest",
    "render_as",
    "render_document_v1",
    "resolve_asset_path",
    "routing_body_digest",
    "schema_path",
    "transform_0_2_to_1_0",
    "validate_assets",
    "validate_frontmatter_dict",
    "validate_frontmatter_v1",
    "validate_version_constraint",
    "write_engram_as",
]
