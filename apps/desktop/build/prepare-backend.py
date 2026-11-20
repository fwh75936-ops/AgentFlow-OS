"""Prepare the bundled Windows backend for the packaged desktop app.

Produces apps/desktop/backend-bundle/ with:
  python/     — python.org embeddable Python (amd64), ._pth patched to
                include Lib\\site-packages and ..\\agent-core, site enabled
  wheels are extracted into python/Lib/site-packages (Windows amd64
  binaries fetched with pip download --platform win_amd64)
  agent-core/ — services/agent-core source (app/, run.py), no dev data
  seed/agentflow.db — sanitized copy of the dev database: model provider
                API keys wiped, machine-specific absolute paths removed

Run on the Linux build VM; downloads from python.org + PyPI.
The dev database itself is never modified — sanitization happens on the copy.
"""
import glob
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(DESKTOP))
CORE_SRC = os.path.join(REPO, "services", "agent-core")
BUNDLE = os.path.join(DESKTOP, "backend-bundle")
CACHE = os.path.join(HERE, ".backend-cache")
PY_VERSION = "3.12.8"
PY_ZIP = f"python-{PY_VERSION}-embed-amd64.zip"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP}"

RUNTIME_DEPS = [
    "fastapi", "uvicorn", "sqlalchemy", "pydantic", "httpx", "jsonschema",
    "pypdf", "python-docx", "socksio", "python-multipart",
    # uvicorn[standard] minus uvloop (no Windows build exists / not needed):
    "websockets", "httptools", "python-dotenv", "pyyaml", "watchfiles",
]


def download(url: str, dest: str) -> None:
    if os.path.exists(dest):
        print(f"cached: {dest}")
        return
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def main() -> None:
    os.makedirs(CACHE, exist_ok=True)
    if os.path.isdir(BUNDLE):
        shutil.rmtree(BUNDLE)
    os.makedirs(BUNDLE)

    # --- 1. embeddable python -------------------------------------------------
    py_zip_path = os.path.join(CACHE, PY_ZIP)
    download(PY_URL, py_zip_path)
    py_dir = os.path.join(BUNDLE, "python")
    os.makedirs(py_dir)
    with zipfile.ZipFile(py_zip_path) as z:
        z.extractall(py_dir)
    # Unpack the stdlib zip into Lib/: loose .py files compress far
    # better under the installer's LZMA than a pre-zipped archive,
    # which measurably shrinks the final installer.
    stdlib_zip = os.path.join(py_dir, "python312.zip")
    if os.path.exists(stdlib_zip):
        lib_dir = os.path.join(py_dir, "Lib")
        os.makedirs(lib_dir, exist_ok=True)
        with zipfile.ZipFile(stdlib_zip) as z:
            z.extractall(lib_dir)
        os.remove(stdlib_zip)
        print("unpacked stdlib into Lib/ for better installer compression")
    pth = os.path.join(py_dir, f"python{PY_VERSION[:4].replace('.', '')}._pth")
    pth_lines = [
        "Lib",
        ".",
        "Lib\\site-packages",
        "..\\agent-core",
        "import site",
        "",
    ]
    with open(pth, "w", encoding="utf-8") as f:
        f.write("\n".join(pth_lines))
    print(f"patched {pth}")

    # --- 2. windows wheels -> site-packages ------------------------------------
    wheels_dir = os.path.join(CACHE, "wheels")
    os.makedirs(wheels_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--dest", wheels_dir,
        "--platform", "win_amd64",
        "--python-version", "3.12",
        "--implementation", "cp",
        "--abi", "cp312",
        "--only-binary=:all:",
        *RUNTIME_DEPS,
    ]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    site_packages = os.path.join(py_dir, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    wheel_names = sorted(os.listdir(wheels_dir))
    for name in wheel_names:
        if name.endswith(".whl"):
            with zipfile.ZipFile(os.path.join(wheels_dir, name)) as z:
                z.extractall(site_packages)
    print(f"extracted {len(wheel_names)} wheels into site-packages")

    # Trim parts the backend never imports, to keep the installer lean
    # (the bundle smoke test imports the full app and fails loudly if
    # a trim ever cuts something real):
    # - SQLAlchemy dialects other than sqlite (lazy-loaded by URL)
    sa_dialects = os.path.join(site_packages, "sqlalchemy", "dialects")
    for name in ("mysql", "postgresql", "oracle", "mssql"):
        target = os.path.join(sa_dialects, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target + ".py"):
            os.remove(target + ".py")
    # - lxml subpackages python-docx does not use (it needs etree)
    for sub in ("html", "isoschematron"):
        target = os.path.join(site_packages, "lxml", sub)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
    for pyd in glob.glob(os.path.join(site_packages, "lxml", "objectify.*.pyd")) + \
            glob.glob(os.path.join(site_packages, "lxml", "sax.*.pyd")) + \
            glob.glob(os.path.join(site_packages, "lxml", "builder.*.pyd")) + \
            glob.glob(os.path.join(site_packages, "lxml", "_elementpath.*.pyd")):
        os.remove(pyd)
    for cache in glob.glob(os.path.join(site_packages, "**", "__pycache__"), recursive=True):
        shutil.rmtree(cache, ignore_errors=True)
    print("trimmed unused dialect/lxml parts from site-packages")

    # --- 3. agent-core source ---------------------------------------------------
    core_dst = os.path.join(BUNDLE, "agent-core")
    shutil.copytree(
        CORE_SRC, core_dst,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", "data", "agentflow.db", "tests",
            "*.pyc", ".pytest_cache",
        ),
    )
    print(f"copied agent-core source -> {core_dst}")

    # --- 4. sanitized seed database --------------------------------------------
    seed_dir = os.path.join(BUNDLE, "seed")
    os.makedirs(seed_dir)
    seed_path = os.path.join(seed_dir, "agentflow.db")
    shutil.copy2(os.path.join(CORE_SRC, "agentflow.db"), seed_path)
    db = sqlite3.connect(seed_path)
    cur = db.execute("UPDATE model_providers SET api_key = NULL")
    print(f"seed: wiped api_key on {cur.rowcount} provider row(s)")
    # The seeded example MCP server pointed at a dev-machine absolute path;
    # make it portable (relative to the repo layout) for the seed copy.
    cur = db.execute(
        "UPDATE mcp_servers SET command_or_url = "
        "REPLACE(command_or_url, ?, 'python examples/mcp-server/server.py') "
        "WHERE command_or_url LIKE ?",
        ("/home/hatch/workspace/agentflow-os/", "%/home/%"),
    )
    print(f"seed: rewrote {cur.rowcount} machine-specific MCP path(s)")
    # Sandbox install records describe THIS machine's runtime state
    # (rootfs paths in the install log, a Debian 12 marked installed whose
    # files are not shipped). On a user machine they would be fake state,
    # so the seed starts from the honest product flow: nothing installed,
    # nothing selected; dependency job history goes with them.
    for table in ("sandbox_installs", "dependency_install_jobs", "sandbox_config"):
        cur = db.execute(f"DELETE FROM {table}")
        print(f"seed: cleared {cur.rowcount} machine-local row(s) from {table}")
    db.commit()
    db.execute("VACUUM")
    db.close()
    # Attachment files of imported seed skills (referenced from the seed
    # database's skills.files metadata) travel next to the seed DB;
    # pythonService copies them into the user data dir on first run.
    # The NSIS compiler on this build machine segfaults on non-ASCII
    # file names in the payload, so inside the INSTALLER SEED ONLY the
    # files are stored under ASCII names and the seed metadata is
    # rewritten to match (contents untouched; the dev database and its
    # files keep their original names).
    skill_files_src = os.path.join(CORE_SRC, "data", "skill_files")
    if os.path.isdir(skill_files_src):
        import hashlib
        renames = {}  # (skill_id, metadata path) -> metadata path, seed copy only
        dst_root = os.path.join(seed_dir, "skill_files")
        for dirpath, _dirnames, filenames in os.walk(skill_files_src):
            rel_dir = os.path.relpath(dirpath, skill_files_src)
            for fn in filenames:
                src_file = os.path.join(dirpath, fn)
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                rel_posix = rel.replace(os.sep, "/")
                if any(ord(c) > 127 for c in fn):
                    ext = os.path.splitext(fn)[1]
                    new_fn = f"file-{hashlib.md5(rel_posix.encode('utf-8')).hexdigest()[:6]}{ext}"
                    parts = rel_posix.split("/")
                    if len(parts) >= 2:
                        meta_old = "/".join(parts[1:])
                        meta_new = "/".join(parts[1:-1] + [new_fn])
                        renames[(parts[0], meta_old)] = meta_new
                    rel = os.path.join(os.path.dirname(rel), new_fn) if os.path.dirname(rel) else new_fn
                dst_file = os.path.join(dst_root, rel)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)
        print(f"seed: copied skill attachment files from {skill_files_src}")
        if renames:
            import json as _json
            db = sqlite3.connect(seed_path)
            rows = db.execute("SELECT id, files FROM skills WHERE files IS NOT NULL").fetchall()
            for sid, files_json in rows:
                try:
                    entries = _json.loads(files_json)
                except Exception:
                    continue
                changed = False
                for entry in entries:
                    key = (str(sid), entry.get("path", ""))
                    if key in renames:
                        entry["path"] = renames[key]
                        changed = True
                if changed:
                    db.execute("UPDATE skills SET files = ? WHERE id = ?",
                               (_json.dumps(entries, ensure_ascii=False), sid))
                    print(f"seed: skill {sid} attachment renamed in installer copy only")
            db.commit()
            db.close()
    raw = open(seed_path, "rb").read()
    # /home/agentflow is the sandbox's own product user (legit demo data);
    # what must never ship is credentials and the dev machine's home path.
    for pat in [rb"sk-[A-Za-z0-9]{10,}", rb"AKIA[0-9A-Z]{16}",
                rb"-----BEGIN", rb"/home/hatch"]:
        hits = len(re.findall(pat, raw))
        print(f"seed scan {pat!r}: {hits} hit(s)")
        if hits:
            raise SystemExit(f"seed database still contains pattern {pat!r}")
    print(f"seed db ready: {seed_path} ({len(raw)} bytes)")
    print("bundle complete:", BUNDLE)


if __name__ == "__main__":
    main()
