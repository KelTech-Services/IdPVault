#!/usr/bin/env python3
"""Mint an IdPVault license key (Ed25519-signed token).

KELTECH-INTERNAL TOOL — requires the private signing key, which lives ONLY at
~/.idpvault_license_privkey (never in this repo or image). The same code path
will be used by the idpvault.com subscription backend to auto-issue keys.

Usage:
  python3 mint_license.py --customer "Acme Corp" --tier pro --years 1
  python3 mint_license.py --customer "Acme Corp" --tier pro --years 2 \
      --max-tenants 10 --features identity
  # renewal that must not lose remaining time: anchor on the old expiry (epoch)
  python3 mint_license.py --customer "Acme Corp" --tier pro --years 1 \
      --extend-from 1783123200

Defaults: unlimited tenants, features=identity, 1 year from now.
Requires the 'cryptography' package (run inside the idpvault container if the
host python lacks it).
"""
import argparse
import base64
import json
import os
import sys
import time


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def main() -> int:
    ap = argparse.ArgumentParser(description="Mint an IdPVault license key")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--tier", default="pro")
    ap.add_argument("--years", type=int, default=1, help="license term in years")
    ap.add_argument("--max-tenants", type=int, default=None,
                    help="tenant cap (omit for unlimited)")
    ap.add_argument("--max-users", type=int, default=None,
                    help="user/seat cap (omit for unlimited)")
    ap.add_argument("--features", nargs="*", default=["identity"],
                    help="feature flags (default: identity)")
    ap.add_argument("--extend-from", type=float, default=None,
                    help="epoch seconds of the PREVIOUS expiry — renewal term is "
                         "added to it so early renewal never loses time")
    ap.add_argument("--key-file", default=os.path.expanduser("~/.idpvault_license_privkey"))
    args = ap.parse_args()

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        print("ERROR: 'cryptography' not available — run inside the idpvault container",
              file=sys.stderr)
        return 1

    with open(args.key_file, "rb") as f:
        raw = f.read().strip()
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))

    now = time.time()
    anchor = args.extend_from if args.extend_from and args.extend_from > now else now
    payload = {
        "customer": args.customer,
        "tier": args.tier,
        "max_tenants": args.max_tenants,      # None = unlimited
        "max_users": args.max_users,          # None = unlimited
        "features": args.features,
        "issued": int(now),
        "expires": int(anchor + args.years * 365 * 86400),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    token = f"{b64url(body)}.{b64url(priv.sign(body))}"

    exp_str = time.strftime("%Y-%m-%d", time.gmtime(payload["expires"]))
    print(f"# customer={args.customer} tier={args.tier} "
          f"max_tenants={args.max_tenants or 'unlimited'} "
          f"features={','.join(args.features) or '-'} expires={exp_str}", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
