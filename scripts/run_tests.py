"""Run test modules in isolated Python processes.

Qt multimedia and worker-thread state can outlive a unittest module. Running
each module in a fresh process prevents that state from affecting later
modules and gives every module an explicit timeout.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    modules = sorted(
        f"tests.{path.stem}"
        for path in (root / "tests").glob("test_*.py")
    )
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures: list[str] = []

    for module in modules:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", module, "-q"],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"TIMEOUT {module} (>{args.timeout:g}s)", flush=True)
            failures.append(module)
            continue

        if completed.returncode:
            print(f"FAIL    {module}", flush=True)
            output = (completed.stdout + completed.stderr).strip()
            if output:
                print(output, flush=True)
            failures.append(module)
        else:
            print(f"PASS    {module}", flush=True)

    print(f"\n{len(modules) - len(failures)}/{len(modules)} modules passed")
    if failures:
        print("Failed modules:")
        print("\n".join(f"- {module}" for module in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
