"""A test of one PyTorch test file that runs each case in a process of its own.

Bazel schedules a test target as a single action. To spread the cases of a file
over the machine, this rule runs them in build actions instead: one lists the
cases, then each case gets an action of its own. The test action that remains
only reports what those found.
"""

load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("@rules_python//python:py_binary.bzl", "py_binary")

# What a process that imports torch may use.
CPUS = 4

def _cpus(_os, _input_count):
    return {"cpu": CPUS}

def _run_cases(template_ctx, *, input_directories, output_directories, additional_inputs, tools, additional_params):
    # Bazel calls this once the listing exists, to create the actions that depend
    # on what it holds: one per file, which is one per case.
    results = output_directories["results"]
    for case in input_directories["cases"].children:
        xml = template_ctx.declare_file(case.basename + ".xml", directory = results)
        log = template_ctx.declare_file(case.basename + ".log", directory = results)
        args = template_ctx.args()
        args.add("--run")
        args.add_all([case, xml, log])
        args.add(additional_params["file"])
        template_ctx.run(
            outputs = [xml, log],
            inputs = [case],
            executable = tools["runner"],
            arguments = [args],
            progress_message = "Testing %s %s" % (additional_params["label"], case.basename),
        )

def _torch_test_impl(ctx):
    runner = ctx.attr.runner[DefaultInfo].files_to_run
    env = {
        "MKL_NUM_THREADS": str(CPUS),
        "OMP_NUM_THREADS": str(CPUS),
        # A process would write bytecode into its sandbox, which is thrown away,
        # and writing it takes longer than compiling. What the test depends on
        # comes with bytecode (pyc_collection below).
        "PYTHONDONTWRITEBYTECODE": "1",
        # pytest takes more options from this variable.
        "PYTEST_ADDOPTS": ctx.attr._pytest_args[BuildSettingInfo].value,
    }

    cases = ctx.actions.declare_directory(ctx.label.name + ".cases")
    ctx.actions.run(
        executable = runner,
        arguments = ["--list", cases.path, ctx.attr.file],
        outputs = [cases],
        env = env,
        use_default_shell_env = True,
        mnemonic = "TorchTestList",
        progress_message = "Listing the cases of %{label}",
        resource_set = _cpus,
    )

    results = ctx.actions.declare_directory(ctx.label.name + ".results")
    ctx.actions.map_directory(
        implementation = _run_cases,
        input_directories = {"cases": cases},
        output_directories = {"results": results},
        tools = {"runner": runner},
        additional_params = {"file": ctx.attr.file, "label": str(ctx.label).removeprefix("@@")},
        # A result is a build output, so Bazel keeps it until an input changes,
        # even if the case failed. Changing this variable runs the cases again.
        env = env | {"RERUN": ctx.attr._rerun[BuildSettingInfo].value},
        use_default_shell_env = True,
        # Bounded by --local_resources=gpu in .bazelrc.
        execution_requirements = {"resources:gpu:1": ""},
        mnemonic = "TorchTestRun",
        resource_set = _cpus,
    )

    script = ctx.actions.declare_file(ctx.label.name)
    command = [ctx.executable._reporter.short_path, cases.short_path, results.short_path]
    ctx.actions.write(script, "#!/bin/bash\nexec %s\n" % " ".join(command), is_executable = True)
    runfiles = ctx.runfiles([cases, results])
    runfiles = runfiles.merge(ctx.attr._reporter[DefaultInfo].default_runfiles)
    return [DefaultInfo(executable = script, runfiles = runfiles)]

_torch_test = rule(
    implementation = _torch_test_impl,
    test = True,
    attrs = {
        "runner": attr.label(
            executable = True,
            # Not "exec": the cases run against the torch this build is for.
            cfg = "target",
            doc = "Program that runs the test file: run_test.py with what the file needs.",
        ),
        "file": attr.string(doc = "The test file, as a path in test/."),
        "_pytest_args": attr.label(default = "//tools/bazel:pytest_args"),
        "_rerun": attr.label(default = "//tools/bazel:rerun"),
        "_reporter": attr.label(default = "//tools/bazel:report", executable = True, cfg = "target"),
    },
)

def torch_test(name, srcs, tags = [], target_compatible_with = [], **kwargs):
    """Declares the test of test/<name>.py, with the same name as in test/run_test.py.

    Args:
      name: the test, like "test_nn" or "inductor/test_torchinductor".
      srcs: the Python files under test/ that it imports, itself included.
      tags: tags of the test.
      target_compatible_with: where the test can run.
      **kwargs: what else the file needs, as arguments of py_binary.
    """
    py_binary(
        name = name + ".bin",
        srcs = srcs + ["//tools/bazel:run_test.py"],
        # The whole file in one process: bazel run //test:<name>.bin -- -k <case>
        args = [name + ".py", "--use-pytest"],
        main = "//tools/bazel:run_test.py",
        # Every process would otherwise compile the 3,000 modules it imports.
        pyc_collection = "include_pyc",
        tags = ["manual"],
        target_compatible_with = target_compatible_with,
        **kwargs
    )
    _torch_test(
        name = name,
        # The test action only adds up results.
        size = "small",
        file = name + ".py",
        runner = name + ".bin",
        tags = tags,
        target_compatible_with = target_compatible_with,
    )
