"""Install this user's reviewed Rules and Skills for VS Code Local."""
import argparse
from pathlib import Path
import sys

from installation import Install, read_json, snapshot
from vscode_profile import build, git

PROJECT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply", "rollback", "recover"))
    parser.add_argument("--sources", type=Path, default=PROJECT.parent,
                        help="directory containing the local Rules and Skills Git checkouts")
    parser.add_argument("--home", type=Path, default=Path.home(), help="target user home")
    parser.add_argument("--candidate", action="store_true",
                        help="preview the two locally fetched origin/main commits (plan only)")
    args = parser.parse_args()
    if args.candidate and args.command != "plan":
        parser.error("--candidate is for preview only")
    try:
        home = args.home.expanduser().resolve(strict=True)
        if not home.is_dir():
            raise ValueError(f"Expected a home directory: {home}")
        target = Install(home)
        desired = None
        if args.command in {"plan", "apply"}:
            pins = read_json(PROJECT / "sources.lock.json")
            if set(pins) != {"Rules", "Skills"}:
                raise ValueError("sources.lock.json must pin Rules and Skills")
            if args.candidate:
                pins = {repo: git(args.sources / repo, "rev-parse", "--verify",
                                  "refs/remotes/origin/main^{commit}").decode().strip()
                        for repo in pins}
                print("Candidate commits:", pins)
            desired = snapshot(pins, build(args.sources, pins, read_json(PROJECT / "vscode-local.json")))
        target.run(args.command, desired)
    except (OSError, ValueError, KeyError) as exc:
        print(f"AgentConfig: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
