"""Copy an allowlist of keys from a local .env into GitHub Actions secrets (values are never printed).

    python tools/push_secrets.py [--dry-run]

Anything not in ALLOWED is ignored.
"""
import subprocess
import sys

ALLOWED = ("ANTHROPIC_API_KEY", "META_PAGE_ID", "META_PAGE_ACCESS_TOKEN", "META_IG_USER_ID",
           "ZERNIO_API_KEY", "SUPABASE_URL", "SUPABASE_SECRET_KEY")


def parse(path=".env"):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env = parse()
    for name in ALLOWED:
        if not env.get(name):
            print(f"skip   {name} (empty or missing)")
        elif "--dry-run" in sys.argv:
            print(f"would set {name}")
        else:
            # value via stdin, never argv (argv is visible to other processes)
            subprocess.run(["gh", "secret", "set", name], input=env[name], text=True, check=True, capture_output=True)
            print(f"set    {name}")


if __name__ == "__main__":
    main()
