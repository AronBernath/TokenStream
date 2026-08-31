"""Build compact component and license inventories from a CycloneDX SBOM."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


UNKNOWN_LICENSE = "NOASSERTION"
PACKAGE_ECOSYSTEMS = {
    "apk",
    "cargo",
    "composer",
    "deb",
    "gem",
    "golang",
    "maven",
    "npm",
    "nuget",
    "pypi",
    "rpm",
}


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


def _component_cpes(component: dict[str, Any]) -> list[str]:
    cpe_data = component.get("cpe")
    if isinstance(cpe_data, str):
        return [cpe_data]
    if isinstance(cpe_data, list):
        return sorted(str(cpe) for cpe in cpe_data)
    return []


def _purl_ecosystem(purl: str) -> str:
    if not purl.startswith("pkg:"):
        return ""
    return purl.split("/", maxsplit=1)[0][4:]


def _inventory_component(component: dict[str, Any]) -> dict[str, Any]:
    purl = component.get("purl") or ""
    return {
        "name": component.get("name") or "",
        "version": component.get("version") or "",
        "type": component.get("type") or "",
        "group": component.get("group") or "",
        "bom_ref": component.get("bom-ref") or "",
        "purl": purl,
        "ecosystem": _purl_ecosystem(str(purl)),
        "purls": _component_purls(component),
        "cpe": _component_cpes(component),
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


def _dedupe_key(component: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(component.get("purl") or ""),
        str(component.get("name") or ""),
        str(component.get("version") or ""),
        str(component.get("type") or ""),
    )


def _merge_licenses(licenses: set[str]) -> list[str]:
    if len(licenses) > 1 and UNKNOWN_LICENSE in licenses:
        licenses.remove(UNKNOWN_LICENSE)
    return sorted(licenses) or [UNKNOWN_LICENSE]


def _merge_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for component in components:
        key = _dedupe_key(component)
        if key not in merged:
            merged[key] = {
                **component,
                "bom_refs": sorted({str(component.get("bom_ref") or "")}),
                "occurrence_count": 1,
            }
            continue

        current = merged[key]
        current["occurrence_count"] += 1
        current["bom_refs"] = sorted({str(ref) for ref in current["bom_refs"]} | {str(component.get("bom_ref") or "")})
        current["purls"] = sorted(set(current["purls"]) | set(component["purls"]))
        current["cpe"] = sorted(set(current["cpe"]) | set(component["cpe"]))
        current["licenses"] = _merge_licenses(set(current["licenses"]) | set(component["licenses"]))
        current["source_locations"] = sorted(set(current["source_locations"]) | set(component["source_locations"]))

    return sorted(merged.values(), key=_sort_key)


def _is_package_dependency(component: dict[str, Any]) -> bool:
    return component.get("type") == "library" and component.get("ecosystem") in PACKAGE_ECOSYSTEMS


def _is_github_action(component: dict[str, Any]) -> bool:
    return component.get("type") == "library" and component.get("ecosystem") == "github"


def _license_ref(component: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(component["name"]),
        "version": str(component["version"]),
        "type": str(component["type"]),
        "ecosystem": str(component["ecosystem"]),
        "purl": str(component["purl"]),
        "bom_ref": str(component["bom_ref"]),
    }


def _build_license_inventory(package_components: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    license_components: dict[str, list[dict[str, str]]] = defaultdict(list)
    for component in package_components:
        component_ref = _license_ref(component)
        for license_name in component["licenses"]:
            license_components[str(license_name)].append(component_ref)

    license_counts = Counter(license_name for component in package_components for license_name in component["licenses"])
    return {
        "product": "TokenStream",
        "inventory_type": "package-license",
        "source": "package-inventory.json",
        "generated_at": generated_at,
        "package_count": len(package_components),
        "license_count": len(license_counts),
        "unknown_license_package_count": license_counts.get(UNKNOWN_LICENSE, 0),
        "licenses": [
            {
                "license": license_name,
                "package_count": license_counts[license_name],
                "packages": sorted(license_components[license_name], key=_sort_key),
            }
            for license_name in sorted(license_counts)
        ],
    }


def _build_unknown_license_inventory(
    package_components: list[dict[str, Any]], action_components: list[dict[str, Any]], generated_at: str
) -> dict[str, Any]:
    unknown_packages = [component for component in package_components if UNKNOWN_LICENSE in component["licenses"]]
    unknown_actions = [component for component in action_components if UNKNOWN_LICENSE in component["licenses"]]
    return {
        "product": "TokenStream",
        "inventory_type": "unknown-license-packages",
        "source": "package-inventory.json and github-actions-inventory.json",
        "generated_at": generated_at,
        "unknown_package_count": len(unknown_packages),
        "unknown_github_action_count": len(unknown_actions),
        "packages": unknown_packages,
        "github_actions": unknown_actions,
    }


def build_inventories(sbom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    components = [
        _inventory_component(component) for component in sbom.get("components") or [] if isinstance(component, dict)
    ]
    components.sort(key=_sort_key)

    package_components = _merge_components([component for component in components if _is_package_dependency(component)])
    action_components = _merge_components([component for component in components if _is_github_action(component)])
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

    package_inventory = {
        "product": "TokenStream",
        "inventory_type": "package",
        "source": "component-inventory.json",
        "generated_at": generated_at,
        "package_count": len(package_components),
        "ecosystems": dict(Counter(str(component["ecosystem"]) for component in package_components)),
        "packages": package_components,
    }

    github_actions_inventory = {
        "product": "TokenStream",
        "inventory_type": "github-actions",
        "source": "component-inventory.json",
        "generated_at": generated_at,
        "action_count": len(action_components),
        "actions": action_components,
    }

    license_inventory = _build_license_inventory(package_components, generated_at)
    unknown_license_inventory = _build_unknown_license_inventory(package_components, action_components, generated_at)

    return {
        "component": component_inventory,
        "package": package_inventory,
        "github_actions": github_actions_inventory,
        "license": license_inventory,
        "unknown_license": unknown_license_inventory,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive component and license inventories from a CycloneDX SBOM.")
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--component-output", required=True, type=Path)
    parser.add_argument("--package-output", required=True, type=Path)
    parser.add_argument("--github-actions-output", required=True, type=Path)
    parser.add_argument("--license-output", required=True, type=Path)
    parser.add_argument("--unknown-license-output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sbom = json.loads(args.sbom.read_text(encoding="utf-8"))
    inventories = build_inventories(sbom)

    outputs = {
        "component": args.component_output,
        "package": args.package_output,
        "github_actions": args.github_actions_output,
        "license": args.license_output,
        "unknown_license": args.unknown_license_output,
    }
    for inventory_name, output_path in outputs.items():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(inventories[inventory_name], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
