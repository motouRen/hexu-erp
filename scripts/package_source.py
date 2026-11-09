"""Package Git-visible source only; local accounts, databases and QA output stay excluded."""
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root.parent / "hexu-erp-v0.2.0-source.zip"
result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                        cwd=root, check=True, capture_output=True)
paths = sorted(set(path.decode("utf-8") for path in result.stdout.split(b"\0") if path))
for path in paths:
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or (root / path).is_symlink():
        raise RuntimeError("Unsafe source path: " + path)
    if candidate.name in {"LOCAL_ACCESS.txt", ".env", ".local-secret", ".demo-secret"} or ".sqlite3" in candidate.name:
        raise RuntimeError("Local state cannot be packaged: " + path)
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in paths:
        archive.write(root / path, root.name + "/" + path)
print(f"Packaged {len(paths)} source files: {destination}")
