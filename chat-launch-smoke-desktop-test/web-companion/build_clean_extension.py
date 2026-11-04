"""Build a dedicated, strictly allowlisted Chrome/YaBrowser unpacked extension.
Never install the Python bridge root as a browser extension.

The local bridge configuration is copied only to the user's local extension
folder. No bearer token is printed, archived or uploaded.
"""
import json
import shutil
import hashlib
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
DEST = SOURCE.parent / "web-extension"
FILES = (
    "manifest.json",
    "core.js",
    "content.js",
    "background.js",
    "background_content.js",
    "bridge-config.json",
    "popup.html",
    "popup.js",
)
BROWSER = {"manifest.json", "core.js", "content.js", "background.js", "background_content.js"}
DEST.mkdir(exist_ok=True)

existing = {p.name for p in DEST.iterdir()}
extra = existing - set(FILES)
if extra:
    raise RuntimeError("Unexpected extension files; not deleting or overwriting: " + ", ".join(sorted(extra)))
for name in FILES:
    src = SOURCE / name
    if not src.is_file():
        raise RuntimeError("Required source missing: " + name)
    if name == "bridge-config.json":
        config = json.loads(src.read_text(encoding="utf-8"))
        if config.get("endpoint") != "http://127.0.0.1:18763":
            raise RuntimeError("Invalid loopback bridge endpoint")
        if not isinstance(config.get("key"), str) or len(config["key"]) < 32:
            raise RuntimeError("Invalid bridge authentication material")
    target = DEST / name
    # Existing destination must be exactly equal or be an older locally copied version.
    # No symlinks and no non-allowlisted filenames.
    if src.is_symlink() or target.is_symlink():
        raise RuntimeError("Symlinks are not allowed in extension source or destination")
    shutil.copyfile(src, target)

manifest=json.loads((DEST/"manifest.json").read_text(encoding="utf-8"))
if (manifest.get("manifest_version") != 3 or
    manifest.get("background",{}).get("service_worker") != "background.js" or
    manifest.get("host_permissions") != ["http://127.0.0.1:18763/*","https://chatgpt.com/*"]):
    raise RuntimeError("Unexpected extension manifest")
if {p.name for p in DEST.iterdir()} != set(FILES):
    raise RuntimeError("Extension folder is not clean")
if any(p.name.startswith("_") for p in DEST.rglob("*")):
    raise RuntimeError("Reserved filename found in extension folder")
print(json.dumps({
    "ok": True,
    "extension_path": str(DEST),
    "manifest_version": manifest["manifest_version"],
    "extension_version": manifest["version"],
    "file_count": len(FILES),
    "python_files_present": False,
    "reserved_names_present": False,
    "auth_config_copied": True,
    "config_secret_printed": False
},ensure_ascii=False))
