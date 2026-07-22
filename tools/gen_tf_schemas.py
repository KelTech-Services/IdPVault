#!/usr/bin/env python3
"""Regenerate the trimmed Terraform provider schemas committed at
backend/app/core/tfschemas/{authentik,okta,auth0}.json.

Runs the official providers' own schema dump (terraform providers schema -json)
in docker, then trims it to what the export engine needs: per resource, the
attribute names with type and required/computed/sensitive flags, plus nested
block schemas. Descriptions are dropped (that is most of the raw dump's bulk).

Usage (from repo root, needs docker):
    python3 tools/gen_tf_schemas.py            # full run: dump via docker, then trim
    python3 tools/gen_tf_schemas.py dump.json  # trim a pre-dumped schemas.json
"""
import json
import os
import subprocess
import sys
import tempfile

PROVIDERS = {
    "registry.terraform.io/goauthentik/authentik": "authentik",
    "registry.terraform.io/okta/okta": "okta",
    "registry.terraform.io/auth0/auth0": "auth0",
}
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "backend", "app", "core", "tfschemas")

MAIN_TF = """terraform {
  required_providers {
    authentik = { source = "goauthentik/authentik" }
    okta      = { source = "okta/okta" }
    auth0     = { source = "auth0/auth0" }
  }
}
"""


def dump_schemas() -> dict:
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "main.tf"), "w") as f:
            f.write(MAIN_TF)
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{d}:/w", "-w", "/w",
             "--entrypoint", "sh", "hashicorp/terraform:latest", "-c",
             "terraform init -backend=false >/dev/null"
             " && terraform providers schema -json > schemas.json"],
            check=True)
        with open(os.path.join(d, "schemas.json")) as f:
            return json.load(f)


def trim_block(block: dict) -> dict:
    out = {"attrs": {}, "blocks": {}}
    for name, a in (block.get("attributes") or {}).items():
        out["attrs"][name] = {
            "type": a.get("type"),
            "req": bool(a.get("required")),
            "opt": bool(a.get("optional")),
            "computed": bool(a.get("computed")),
            "sensitive": bool(a.get("sensitive")),
        }
    for name, b in (block.get("block_types") or {}).items():
        out["blocks"][name] = {"nesting": b.get("nesting_mode"),
                               "schema": trim_block(b.get("block", {}))}
    return out


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    else:
        data = dump_schemas()
    os.makedirs(OUT_DIR, exist_ok=True)
    for src, short in PROVIDERS.items():
        ps = data["provider_schemas"][src]
        trimmed = {
            "provider_source": src.removeprefix("registry.terraform.io/"),
            "resources": {r: trim_block(s.get("block", {}))
                          for r, s in ps.get("resource_schemas", {}).items()},
        }
        out = os.path.join(OUT_DIR, f"{short}.json")
        with open(out, "w") as f:
            json.dump(trimmed, f, separators=(",", ":"), sort_keys=True)
        print(f"{short}: {len(trimmed['resources'])} resources -> {out}"
              f" ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
