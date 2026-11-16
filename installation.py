"""Reconcile owned files. A durable journal can finish an interrupted update."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile

from vscode_profile import RULE_DIR, SKILL_DIR, safe_parts

EMPTY = {"pins": {}, "files": {}}


def snapshot(pins, files):
    return {"pins": pins, "files": {k: base64.b64encode(v).decode() for k, v in files.items()}}


def reject_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Link or reparse point: {path}")


def checked(home, relative):
    path = home
    for part in relative.split("/"):
        if part in {"", ".", ".."} or "/" in part or "\\" in part or ":" in part:
            raise ValueError(f"Invalid relative path: {relative}")
        path = path / part
        reject_link(path)
    return path


def unit(path):
    if path.startswith(RULE_DIR + "/"):
        safe_parts(path[len(RULE_DIR) + 1:])
        return RULE_DIR
    if path.startswith(SKILL_DIR + "/"):
        name, sep, relative = path[len(SKILL_DIR) + 1:].partition("/")
        safe_parts(f"{name}/{relative}")
        if sep:
            return f"{SKILL_DIR}/{name}"
    raise ValueError(f"Path outside managed directories: {path}")


def atomic_write(path, data, staging=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stage payloads beside their managed directory. A killed process must
    # not leave a temporary file inside a skill that blocks journal replay.
    with tempfile.NamedTemporaryFile(dir=staging or path.parent, suffix=".tmp", delete=False) as file:
        tmp = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, obj):
    atomic_write(path, json.dumps(obj, sort_keys=True).encode())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield  # Closing releases the OS lock even if the process exits.


def inspect(home, old, new, recovering=False):
    """Preflight every owned unit before touching the first file."""
    old_units = {unit(p) for p in old}
    known = old.keys() | new.keys()
    for folder in sorted({unit(p) for p in known}):
        root = checked(home, folder)
        if not root.exists():
            continue
        if not root.is_dir() or (folder not in old_units and not recovering):
            raise ValueError(f"Unmanaged target exists: {root}")
        for parent, dirs, names in os.walk(root):
            for name in dirs + names:
                reject_link(Path(parent, name))
            for name in names:
                path = Path(parent, name)
                if path.relative_to(home).as_posix() not in known:
                    raise ValueError(f"Unmanaged file inside target: {path}")

    changes = []
    for name in sorted(known):
        path = checked(home, name)
        actual = None
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Expected a regular file: {path}")
            actual = base64.b64encode(path.read_bytes()).decode()
        before, after = old.get(name), new.get(name)
        if actual != before and not (recovering and actual == after):
            raise ValueError(f"Local change or filename collision: {path}")
        if actual != after:
            action = "remove" if after is None else "add" if actual is None else "update"
            changes.append((action, name))
    return changes


class Install:
    def __init__(self, home):
        self.home = home
        self.state = checked(home, ".agentconfig/state.json")
        self.pending = checked(home, ".agentconfig/pending.json")
        self.lock = checked(home, ".agentconfig/write.lock")

    def current(self):
        return read_json(self.state) if self.state.exists() else {"active": EMPTY, "previous": None}

    def finish(self, transition):
        old = transition["previous"]["files"]
        new = transition["active"]["files"]
        changes = inspect(self.home, old, new, recovering=True)
        for action, name in changes:
            path = checked(self.home, name)
            if action == "remove":
                path.unlink()
            else:
                atomic_write(path, base64.b64decode(new[name], validate=True),
                             staging=(self.home / unit(name)).parent)
        # Remove empty directories left by retired files, including after a retry.
        for name in old.keys() - new.keys():
            parent = checked(self.home, name).parent
            boundary = self.home / unit(name)
            while parent == boundary or boundary in parent.parents:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                parent = parent.parent
        write_json(self.state, transition)
        self.pending.unlink()

    def run(self, command, desired=None):
        if command == "plan":
            return self.change(command, desired)
        with locked(self.lock):
            if command == "recover":
                if self.pending.exists():
                    self.finish(read_json(self.pending))
                print("Installation consistent.")
                return
            self.change(command, desired)

    def change(self, command, desired):
        if self.pending.exists():
            raise ValueError("Interrupted operation. Run recover first.")
        state = self.current()
        old = state["active"]
        if command == "rollback":
            desired = state["previous"]
            if desired is None:
                raise ValueError("No previous installation.")
        changes = inspect(self.home, old["files"], desired["files"])
        for action, name in changes:
            print(f"{action:7} {name}")
        if desired == old:
            print("Up to date.")
            return
        print("Source revisions:", json.dumps(desired["pins"], sort_keys=True))
        if command != "plan":
            transition = {"active": desired, "previous": old}
            write_json(self.pending, transition)
            self.finish(transition)
            print(f"Applied {len(changes)} file changes.")
