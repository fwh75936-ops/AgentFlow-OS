"""Skill package import: the de-facto agent-skill zip layout.

A package is a .zip holding one or more skill directories; each skill
directory contains a SKILL.md whose YAML frontmatter carries at least
`name` (description expected too) and whose body is the instruction
text. Sibling files (scripts/, references/, assets …) are stored with
the skill and listed in its detail — stored and shown, never executed
by the import itself.

Safety: the upload is size-capped, the entry count is capped, total
uncompressed size is capped, and every entry path is normalized and
checked so nothing can escape the skill's own directory (no absolute
paths, no `..`). A bad skill inside a collection is skipped with a
reason; the rest still import. Nothing is silently dropped.
"""
import io
import os
import re
import zipfile

from app import models

MAX_UPLOAD_BYTES = 20 * 1024 * 1024        # 20 MB compressed
MAX_ENTRIES = 200
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024  # 50 MB across all entries
MAX_FILE_BYTES = 10 * 1024 * 1024          # 10 MB per single file

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def skill_files_root() -> str:
    root = os.environ.get("AGENTFLOW_SKILLS_DIR",
                          os.path.join(_BASE_DIR, "data", "skill_files"))
    os.makedirs(root, exist_ok=True)
    return root


def _safe_relpath(name: str) -> str | None:
    """Normalize a zip entry name to a safe relative path, or None when
    the entry tries to escape (absolute path, drive, or .. segments)."""
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[a-zA-Z]:", name):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def parse_frontmatter(text: str):
    """Minimal frontmatter reader for the simple `key: value` scalars a
    SKILL.md carries (name / description / version). Returns (meta, body)
    or (None, body) when there is no frontmatter block."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    block = text[3:end]
    body = text[end + 4:].lstrip("\n")
    meta = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line.strip())
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[m.group(1).lower()] = value
    return meta, body


def import_skill_package(db, raw: bytes):
    """Import every skill found in the zip bytes. Returns a per-item
    report: {"imported": [{id, name, files}], "skipped": [{where, reason}]}.
    Raises ValueError for whole-package rejections (not a zip, over a
    hard limit) — those are honest failures, not partial imports."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"技能包过大：{len(raw) // 1024} KB，上限 20 MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("这不是一个有效的 zip 文件")
    all_infos = zf.infolist()
    if len(all_infos) > MAX_ENTRIES:
        raise ValueError(f"包内条目过多：{len(all_infos)} 个，上限 {MAX_ENTRIES} 个")
    infos = [i for i in all_infos if not i.is_dir()]
    total = sum(i.file_size for i in infos)
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ValueError(f"解压后总大小过大：{total // (1024 * 1024)} MB，上限 50 MB")

    # Read everything into memory with path safety enforced per entry.
    contents: dict[str, bytes] = {}
    unsafe: list[str] = []
    for info in infos:
        rel = _safe_relpath(info.filename)
        if rel is None:
            unsafe.append(info.filename)
            continue
        if info.file_size > MAX_FILE_BYTES:
            unsafe.append(f"{info.filename}（单文件超过 10 MB）")
            continue
        if rel in contents:
            unsafe.append(f"{info.filename}（与先前条目路径重复，已跳过）")
            continue
        try:
            contents[rel] = zf.read(info)  # by ZipInfo: duplicate names stay distinct
        except Exception as e:
            unsafe.append(f"{info.filename}（条目读取失败：{e}）")

    # Discover skill directories: every directory holding a SKILL.md.
    skill_dirs: dict[str, str] = {}   # dir prefix -> SKILL.md path
    for rel in contents:
        if rel.rsplit("/", 1)[-1].lower() == "skill.md":
            prefix = rel.rsplit("/", 1)[0] if "/" in rel else ""
            skill_dirs[prefix] = rel
    top_dirs = sorted({rel.split("/", 1)[0] for rel in contents if "/" in rel})

    imported, skipped = [], []
    created_bases: list[str] = []
    for entry in unsafe:
        skipped.append({"where": entry, "reason": "路径不安全或文件过大，已跳过"})
    for d in top_dirs:
        covered = ("" in skill_dirs or d in skill_dirs
                   or any(p and d.startswith(p + "/") for p in skill_dirs)
                   or any(p.startswith(d + "/") for p in skill_dirs))
        if not covered:
            skipped.append({"where": d, "reason": "目录里缺少 SKILL.md，不是技能目录"})
    existing_names = {s.name for s in db.query(models.Skill).all()}
    try:
        for prefix in sorted(skill_dirs):
            where = prefix or "（包根目录）"
            try:
                text = contents[skill_dirs[prefix]].decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"where": where, "reason": "SKILL.md 不是 UTF-8 文本"})
                continue
            meta, body = parse_frontmatter(text)
            if meta is None:
                skipped.append({"where": where, "reason": "SKILL.md 缺少开头的属性区（frontmatter）"})
                continue
            name = (meta.get("name") or "").strip()
            if not name:
                skipped.append({"where": where, "reason": "SKILL.md 的属性区缺少 name（技能名）"})
                continue
            if name in existing_names:
                skipped.append({"where": where, "reason": f"已存在同名技能「{name}」，未重复导入"})
                continue
            skill = models.Skill(
                name=name,
                description=(meta.get("description") or "").strip(),
                instructions=body.strip(),
                version=(meta.get("version") or "1.0.0").strip() or "1.0.0",
                required_tools=[], permissions={}, enabled=True, trusted=False,
                source="import", files=[])
            db.add(skill); db.flush()
            # Store bundled files under the skill's own directory. A file
            # belongs to its NEAREST skill ancestor: a nested skill's
            # files are not also harvested into the parent skill.
            stored = []
            base = os.path.join(skill_files_root(), str(skill.id))
            created_bases.append(base)
            base_real = os.path.realpath(base)
            for rel, data in sorted(contents.items()):
                if prefix:
                    if not rel.startswith(prefix + "/"):
                        continue
                    sub = rel[len(prefix) + 1:]
                else:
                    sub = rel
                if sub.lower() == "skill.md":
                    continue
                owner = max((p for p in skill_dirs
                             if p == "" or rel == p or rel.startswith(p + "/")),
                            key=len, default=None)
                if owner != prefix:
                    continue  # a deeper skill owns this file
                dest = os.path.join(base, *sub.split("/"))
                # Containment re-checked on the resolved path: a symlink
                # anywhere along it must not lead outside this skill's dir.
                if os.path.commonpath([base_real, os.path.realpath(dest)]) != base_real:
                    skipped.append({"where": rel, "reason": "附件路径超出技能目录，已跳过"})
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(data)
                stored.append({"path": sub, "size": len(data)})
            skill.files = stored
            existing_names.add(name)
            imported.append({"id": skill.id, "name": name, "files": len(stored)})
        db.commit()
    except Exception:
        # Database and disk must agree: a failed import removes the
        # attachment directories it created along with the DB rows.
        db.rollback()
        import shutil
        for base in created_bases:
            shutil.rmtree(base, ignore_errors=True)
        raise
    return {"imported": imported, "skipped": skipped}
