"""Runs one PyTorch test file the way test/run_test.py does: from test/, as a script.

    run_test.py <file> <options>                  runs the file
    run_test.py --list <directory> <file>         writes a file for each of its cases
    run_test.py --run <case> <xml> <log> <file>   runs the case that file names

The last two are the actions of torch_test.bzl.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


# The limit common_utils gives a case that it runs in a process of its own.
CASE_TIMEOUT = 15 * 60
PYTHON = [sys.executable, "-bb"]


def list_cases(file: str, directory: Path) -> None:
    """Writes the pytest id of each case to a file that is named after the case."""
    command = [*PYTHON, file, "--use-pytest", "--collect-only", "-q"]
    listing = subprocess.run(command, capture_output=True, timeout=CASE_TIMEOUT)
    lines = listing.stdout.decode(errors="replace").splitlines()
    cases = [line for line in lines if line.startswith(f"test/{file}::")]
    if not cases and listing.returncode != 0:
        # Running the file is what reports why it has no cases to list.
        cases = [f"test/{file}"]
    names: set[str] = set()
    for case in cases:
        name = case.removeprefix("test/").removeprefix(f"{file}::").replace("::", ".")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        if len(name) > 200 or name in names:
            # Too long for a file name, or by now the same as another case's.
            name = f"{name[:190]}~{len(names)}"
        names.add(name)
        Path(directory, name).write_text(case)


def error(case: str, message: str) -> ET.Element:
    """A test suite of one failed case, named the way pytest's report names it."""
    # The type checker's environment has no pytest.
    # pyrefly: ignore [missing-import]
    from _pytest.junitxml import mangle_test_address

    *classes, name = mangle_test_address(case)
    suite = ET.Element("testsuite", name="run_test.py")
    testcase = ET.SubElement(suite, "testcase", classname=".".join(classes), name=name)
    ET.SubElement(testcase, "error", message=message)
    return suite


def run_case(file: str, case: Path, result: Path, log: Path) -> None:
    """Runs the case and reports it in `result`, even when its process dies."""
    report = Path(tempfile.mkdtemp(), "report.xml")
    select = [f"--nodeid-file={case}", f"--junit-xml-reruns={report}"]
    command = [*PYTHON, file, "--use-pytest", *select]
    with open(log, "wb") as out:
        test = subprocess.Popen(command, stdout=out, stderr=out, start_new_session=True)
        try:
            ending = f"exit code {test.wait(timeout=CASE_TIMEOUT)}"
        except subprocess.TimeoutExpired:
            os.killpg(test.pid, signal.SIGKILL)
            test.wait()
            ending = f"no result after {CASE_TIMEOUT} s"
    suites = ET.Element("testsuites")
    # pytest writes its report once it is through the case.
    if report.exists():
        suites.extend(ET.parse(report).getroot())
    if not report.exists() or ending not in ("exit code 0", "exit code 1"):
        suites.append(error(case.read_text(), ending))
    ET.ElementTree(suites).write(result)


if __name__ == "__main__":
    args = sys.argv[1:]
    start = Path.cwd()
    # Bazel's Python launcher exports PYTHONSAFEPATH, but tests import the
    # helper modules that sit next to them.
    os.environ.pop("PYTHONSAFEPATH", None)
    # An activated venv has its interpreter and its wheels' scripts on PATH, and
    # tests launch python, python3 and ninja by name. Bazel's venv has only a
    # python3, and it keeps each wheel in its own directory, scripts in bin/.
    python = Path(tempfile.mkdtemp(), "python")
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    wheels = [Path(p).parent for p in sys.path if Path(p).name == "site-packages"]
    scripts = [str(wheel / "bin") for wheel in wheels if (wheel / "bin").is_dir()]
    paths = [str(python.parent), os.path.dirname(sys.executable), *scripts]
    os.environ["PATH"] = os.pathsep.join([*paths, os.environ["PATH"]])
    os.chdir(Path(os.environ["RUNFILES_DIR"], "_main/test"))
    if args[0] not in ("--list", "--run"):
        os.execv(sys.executable, [*PYTHON, *args])
    # Bazel gives a test action an empty home directory, and a build action none.
    os.environ["HOME"] = tempfile.mkdtemp()
    if args[0] == "--list":
        list_cases(args[2], start / args[1])
    else:
        run_case(args[4], *(start / path for path in args[1:4]))
