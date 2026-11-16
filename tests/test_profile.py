import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from installation import Install, read_json
from vscode_profile import RULE_DIR, SKILL_DIR

PROJECT = Path(__file__).resolve().parents[1]


class ProfileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "isolated home"
        self.home.mkdir()
        self.cli = self.root / "AgentConfig"
        self.cli.mkdir()
        for name in ("agentconfig.py", "installation.py", "vscode_profile.py", "vscode-local.json"):
            shutil.copyfile(PROJECT / name, self.cli / name)
        for repo in ("Rules", "Skills"):
            (self.root / repo).mkdir()
            self.git(repo, "init", "-q")
        for rule in ("00-core", "80-agent-skills", "20-python-framework"):
            (self.root / "Rules" / f"{rule}.md").write_text(rule)
        self.skill("sample")
        self.pin()

    def git(self, repo, *args):
        return subprocess.check_output([
            "git", "-C", str(self.root / repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
            *args
        ], text=True).strip()

    def skill(self, name):
        folder = self.root / "Skills" / ".agents/skills" / name
        (folder / "references").mkdir(parents=True)
        (folder / "references/guide.md").write_text("reviewed")
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use when requested\n---\n[Guide](references/guide.md)\n"
        )
        return folder

    def pin(self):
        pins = {}
        for repo in ("Rules", "Skills"):
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "--allow-empty", "-qm", "review")
            pins[repo] = self.git(repo, "rev-parse", "HEAD")
            self.git(repo, "update-ref", "refs/remotes/origin/main", pins[repo])
        (self.cli / "sources.lock.json").write_text(json.dumps(pins))

    def cli_run(self, *args, success=True):
        proc = subprocess.run([sys.executable, str(self.cli / "agentconfig.py"), *args,
                               "--home", str(self.home)], text=True, capture_output=True,
                              cwd=self.home)
        self.assertEqual(0 if success else 1, proc.returncode, proc.stdout + proc.stderr)
        return proc.stdout if success else proc.stderr

    def test_release_lifecycle(self):
        # The working tree may contain edits that have never been reviewed.
        (self.root / "Rules/00-core.md").write_text("unreviewed")
        self.assertIn("add", self.cli_run("plan"))
        self.assertEqual([], list(self.home.iterdir()))
        self.cli_run("apply")
        skillfile = self.home / SKILL_DIR / "sample/SKILL.md"
        self.assertIn("disable-model-invocation: true", skillfile.read_text())
        core = self.home / RULE_DIR / "00-core.instructions.md"
        self.assertNotIn("unreviewed", core.read_text())
        py = self.home / RULE_DIR / "20-python-framework.instructions.md"
        self.assertIn("**/*.py", py.read_text())
        self.assertIn("Up to date", self.cli_run("apply"))

        lockfile = self.cli / "sources.lock.json"
        old = lockfile.read_bytes()
        (self.root / "Rules/20-python-framework.md").unlink()
        shutil.rmtree(self.root / "Skills/.agents/skills/sample")
        self.skill("replacement")
        self.pin()
        new = lockfile.read_bytes()
        lockfile.write_bytes(old)
        self.assertIn("remove", self.cli_run("plan", "--candidate"))
        self.assertIn("Up to date", self.cli_run("plan"))
        lockfile.write_bytes(new)
        self.cli_run("apply")
        self.assertFalse(py.exists())
        self.assertFalse(skillfile.parent.exists())
        replacement = self.home / SKILL_DIR / "replacement"
        self.assertTrue((replacement / "references/guide.md").is_file())
        self.cli_run("rollback")
        self.assertTrue(skillfile.is_file())
        self.assertTrue(py.is_file())
        self.assertFalse(replacement.exists())

    def test_conflicts_do_not_partially_apply(self):
        foreign = self.home / SKILL_DIR / "sample"
        foreign.mkdir(parents=True)
        self.assertIn("Unmanaged", self.cli_run("apply", success=False))
        self.assertFalse((self.home / RULE_DIR).exists())
        foreign.rmdir()
        self.cli_run("apply")
        (self.home / SKILL_DIR / "sample/SKILL.md").write_text("my local edit")
        (self.root / "Rules/00-core.md").write_text("new")
        self.pin()
        self.assertIn("Local change", self.cli_run("apply", success=False))
        self.assertNotIn("new", (self.home / RULE_DIR / "00-core.instructions.md").read_text())

    def test_new_rule_needs_one_route(self):
        (self.root / "Rules/24-go.md").write_text("Go")
        self.pin()
        self.assertIn("24-go.md", self.cli_run("apply", success=False))
        self.assertFalse((self.home / ".copilot").exists())
        routes = self.cli / "vscode-local.json"
        mapping = read_json(routes)
        mapping["24-go.md"] = {"applyTo": "**/*.go"}
        routes.write_text(json.dumps(mapping))
        self.cli_run("apply")
        self.assertIn("**/*.go", (self.home / RULE_DIR / "24-go.instructions.md").read_text())

    def test_interrupted_apply_is_recoverable(self):
        self.cli_run("apply")
        install = Install(self.home)
        installed = install.current()["active"]
        original = Path.unlink
        removed = []

        def interrupt_second_removal(path, *args, **kwargs):
            if path.suffix == ".md":
                if removed:
                    raise OSError("simulated interruption")
                removed.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupt_second_removal):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                install.run("rollback")
        self.assertTrue(install.pending.is_file())
        self.assertEqual(installed, install.current()["active"])
        self.assertFalse(removed[0].exists())
        self.assertIn("recover", self.cli_run("apply", success=False))
        self.cli_run("recover")
        self.assertFalse((self.home / RULE_DIR).exists())
        self.cli_run("rollback")
        self.assertEqual(installed, install.current()["active"])

    def test_missing_reference_prevents_materialization(self):
        (self.root / "Skills/.agents/skills/sample/references/guide.md").unlink()
        self.pin()
        self.assertIn("missing local reference", self.cli_run("apply", success=False))
        self.assertFalse((self.home / ".copilot").exists())

    def test_update_refreshes_assets_and_preserves_sibling_skills(self):
        self.cli_run("apply")
        foreign = self.home / SKILL_DIR / "my-skill/SKILL.md"
        foreign.parent.mkdir()
        foreign.write_text("mine")
        asset = "sample/references/guide.md"
        (self.root / "Skills/.agents/skills" / asset).write_text("new guide")
        self.pin()
        self.cli_run("apply")
        self.assertEqual("new guide", (self.home / SKILL_DIR / asset).read_text())
        self.cli_run("rollback")
        self.assertEqual("reviewed", (self.home / SKILL_DIR / asset).read_text())
        self.assertEqual("mine", foreign.read_text())

    def test_preexisting_link_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.home / ".copilot").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation requires platform privileges")
        self.assertIn("Link", self.cli_run("apply", success=False))
        self.assertEqual([], list(outside.iterdir()))
