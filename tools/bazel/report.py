"""Reports what the actions of a torch_test found: the test passes if no case failed.

Usage: report.py <directory with a file per case> <directory of their results>
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


if __name__ == "__main__":
    suites = ET.Element("testsuites")
    failed = 0
    # Not every file in the results directory: Bazel leaves those of earlier
    # runs there.
    for listed in sorted(Path(sys.argv[1]).iterdir()):
        result = Path(sys.argv[2], listed.name)
        suite = ET.parse(f"{result}.xml").getroot()
        for case in suite.iter("testcase"):
            if problems := case.findall("failure") + case.findall("error"):
                failed += 1
                # A file that cannot be collected is a case without a class.
                name = ".".join(filter(None, [case.get("classname"), case.get("name")]))
                # bazel-bin/ is where these files are once the build is over.
                print(f"FAILED {name} (bazel-bin/{result}.log)")
                print(*(p.text or p.get("message") for p in problems), sep="\n")
        suites.extend(suite)
    cases = suites.findall("testsuite/testcase")
    seconds = sum(float(case.get("time", 0)) for case in cases)
    ET.ElementTree(suites).write(os.environ["XML_OUTPUT_FILE"])
    print(f"{len(cases)} cases, {failed} failed, {seconds:.0f} s")
    sys.exit(1 if failed else 0)
