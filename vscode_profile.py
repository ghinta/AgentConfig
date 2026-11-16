"""Build a VS Code Local profile from two pinned Git object trees."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

RULE_DIR = ".copilot/instructions/ghinta"
SKILL_DIR = ".copilot/skills"
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RULE_NAME = re.compile(r"[0-9]{2}-[a-z0-9-]+\.md")
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{stem}{n}" for stem in ("COM", "LPT") for n in range(1, 10)
}
ASSETS = {".md", ".txt", ".json", ".yaml", ".yml", ".png", ".svg", ".webp"}


def git(repo, *args):
    proc = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        capture_output=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    if proc.returncode:
        raise ValueError(f"{repo}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def files(repo, revision, prefix=""):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(f"{repo.name}: expected a full commit SHA")
    if git(repo, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError(f"{revision} is not a commit")
    args = ["ls-tree", "-rz", revision]
    if prefix:
        args.extend(["--", prefix])
    for entry in git(repo, *args).split(b"\0"):
        if entry:
            meta, path = entry.decode("utf-8").split("\t", 1)
            mode, _, oid = meta.split()
            yield path.removeprefix(prefix), mode, oid


def safe_parts(path):
    for part in path.split("/"):
        if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
                or part.endswith(".") or part.partition(".")[0].upper() in WINDOWS_DEVICES):
            raise ValueError(f"Unsupported filename: {path}")


def skill_doc(data, name):
    source = data.decode().replace("\r\n", "\n")
    lines = source.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError(f"{name}: missing SKILL.md header")
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        if not sep or key not in {"name", "description"} or key in fields:
            raise ValueError(f"{name}: unsupported SKILL.md metadata")
        fields[key] = value.strip().strip("\"'")
    if (fields.get("name") != name or not 1 <= len(fields.get("description", "")) <= 1024
            or not any(line.strip() for line in lines[end + 1:])):
        raise ValueError(f"{name}: invalid skill name, description or body")
    lines.insert(end, "disable-model-invocation: true")
    return ("\n".join(lines) + "\n").encode()


def rule_doc(name, data, fallback):
    lines = data.decode("utf-8-sig").splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            raise ValueError(f"{name}: unclosed YAML frontmatter") from None
        if not any(re.match(r"^(applyTo|description):[ \t]+\S", line) for line in lines[1:end]):
            raise ValueError(f"{name}: expected applyTo or description in the source frontmatter")
        return data

    if (not isinstance(fallback, dict) or not fallback
            or not fallback.keys() <= {"applyTo", "description"}
            or not all(isinstance(v, str) and v.strip() for v in fallback.values())):
        raise ValueError(f"{name}: add source frontmatter or configure its routing in vscode-local.json")
    header = "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fallback.items())
    return f"---\n{header}---\n\n".encode() + data


def build(sources, pins, routing):
    result = {}
    rules = sources / "Rules"
    for name, mode, oid in files(rules, pins["Rules"]):
        if not RULE_NAME.fullmatch(name):
            continue
        if mode != "100644":
            raise ValueError(f"Unsupported rule entry: {name}")
        target = f"{RULE_DIR}/{name[:-3]}.instructions.md"
        result[target] = rule_doc(name, git(rules, "cat-file", "blob", oid), routing.get(name))
    skills = sources / "Skills"
    names = set()
    for path, mode, oid in files(skills, pins["Skills"], ".agents/skills/"):
        safe_parts(path)
        name, sep, relative = path.partition("/")
        if (not sep or not SKILL_NAME.fullmatch(name) or len(name) > 64 or mode != "100644"
                or Path(relative).suffix.lower() not in ASSETS or "scripts" in relative.lower().split("/")):
            raise ValueError(f"Unsupported skill entry: {path}")
        names.add(name)
        data = git(skills, "cat-file", "blob", oid)
        result[f"{SKILL_DIR}/{path}"] = skill_doc(data, name) if relative == "SKILL.md" else data
    for name in names:
        if f"{SKILL_DIR}/{name}/SKILL.md" not in result:
            raise ValueError(f"{name}: missing SKILL.md")
    for name in names:
        prefix = f"{SKILL_DIR}/{name}/"
        doc = result[prefix + "SKILL.md"].decode()
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", doc):
            path = target.partition("#")[0]
            if not path:
                continue
            safe_parts(path.removeprefix("./"))
            if prefix + str(PurePosixPath(path)) not in result:
                raise ValueError(f"{name}: missing local reference {path}")
    for required in ("00-core", "80-agent-skills"):
        if f"{RULE_DIR}/{required}.instructions.md" not in result:
            raise ValueError(f"Missing baseline rule: {required}")
    if len(result) != len({p.casefold() for p in result}):
        raise ValueError("Case-insensitive filename collision")
    return result
