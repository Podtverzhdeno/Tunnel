"""Read-only check of optional cryptography backend (no package install)."""
import importlib.util
import json
import sys
print(json.dumps({"python":sys.version.split()[0],
                  "cryptography": bool(importlib.util.find_spec("cryptography")),
                  "nacl": bool(importlib.util.find_spec("nacl"))}))
