"""Build compact component and license inventories from a CycloneDX SBOM."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


UNKNOWN_LICENSE = "NOASSERTION"


def _license_value(entry: dict[str, Any]) -> str:
    if expression := entry.get("expression"):
        return str(expression)

    license_data = entry.get("license")
    if isinstance(license_data, dict):
        for key in ("id", "name"):
            if value := license_data.get(key):
                return str(value)

    return UNKNOWN_LICENSE


def _component_licenses(component: dict[str, Any]) -> list[str]:
    licenses = component.get("licenses") or []
    if not isinstance(licenses, list):
        return [UNKNOWN_LICENSE]

    values = sorted({_license_value(item) for item in licenses if isinstance(item, dict)})
    return values or [UNKNOWN_LICENSE]


def _component_locations(component: dict[str, Any]) -> list[str]:
    locations: list[str] = []
    for property_data in component.get("properties") or []:
        if not isinstance(property_data, dict):
            continue
        name = str(property_data.get("name") or "")
        value = property_data.get("value")
        if value and name.startswith("syft:location:"):
            locations.append(str(value))
    return sorted(set(locations))


def _component_purls(component: dict[str, Any]) -> list[str]:
    purls = []
    if purl := component.get("purl"):
        purls.append(str(purl))
    for external_reference in component.get("externalReferences") or []:
        if not isinstance(external_reference, dict):
            continue
        if external_reference.get("type") == "purl" and external_reference.get("url"):
            purls.append(str(external_reference["url"]))
    return sorted(set(purls))


def _inventory_component(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": component.get("name") or "",
        "version": component.get("version") or "",
        "type": component.get("type") or "",
        "group": component.get("group") or "",
        "bom_ref": component.get("bom-ref") or "",
        "purl": component.get("purl") or "",
        "purls": _component_purls(component),
        "cpe": sorted(str(cpe) for cpe in component.get("cpe") or []),
        "licenses": _component_licenses(component),
        "source_locations": _component_locations(component),
    }


def _sort_key(component: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(component.get("name") or "").lower(),
        str(component.get("version") or "").lower(),
        str(component.get("type") or "").lower(),
        str(component.get("purl") or "").lower(),
    )


def build_inventories(sbom: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    components = [
        _inventory_component(component) for component in sbom.get("components") or [] if isinstance(component, dict)
    ]
    components.sort(key=_sort_key)

    license_components: dict[str, list[dict[str, str]]] = defaultdict(list)
    for component in components:
        component_ref = {
            "name": str(component["name"]),
            "version": str(component["version"]),
            "type": str(component["type"]),
            "purl": str(component["purl"]),
            "bom_ref": str(component["bom_ref"]),
        }
        for license_name in component["licenses"]:
            license_components[str(license_name)].append(component_ref)

    license_counts = Counter(license_name for component in components for license_name in component["licenses"])
    generated_at = datetime.now(UTC).isoformat()
    sbom_metadata = {
        "bom_format": sbom.get("bomFormat"),
        "spec_version": sbom.get("specVersion"),
        "serial_number": sbom.get("serialNumber"),
        "version": sbom.get("version"),
    }

    component_inventory = {
        "product": "TokenStream",
        "inventory_type": "component",
        "source": "syft cyclonedx-json",
        "generated_at": generated_at,
        "sbom": sbom_metadata,
        "component_count": len(components),
        "components": components,
    }

    license_inventory = {
        "product": "TokenStream",
        "inventory_type": "license",
        "source": "component-inventory.json",
        "generated_at": generated_at,
        "component_count": len(components),
        "license_count": len(license_counts),
        "unknown_license_component_count": license_counts.get(UNKNOWN_LICENSE, 0),
        "licenses": [
            {
                "license": license_name,
                "component_count": license_counts[license_name],
                "components": sorted(license_components[license_name], key=_sort_key),
            }
            for license_name in sorted(license_counts)
        ],
    }

    return component_inventory, license_inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive component and license inventories from a CycloneDX SBOM.")
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--component-output", required=True, type=Path)
    parser.add_argument("--license-output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sbom = json.loads(args.sbom.read_text(encoding="utf-8"))
    component_inventory, license_inventory = build_inventories(sbom)

    args.component_output.parent.mkdir(parents=True, exist_ok=True)
    args.license_output.parent.mkdir(parents=True, exist_ok=True)
    args.component_output.write_text(
        json.dumps(component_inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.license_output.write_text(
        json.dumps(license_inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
