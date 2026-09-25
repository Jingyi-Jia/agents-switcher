import os
from pathlib import Path
import subprocess
import time

import pytest

from claude_swap.codex import desktop as cd


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if item.name.startswith("test_native_windows_status_reads_harmless_node_arguments"):
        original_run = item.module._NATIVE_RUN
        script = cd._WINDOWS_SCRIPT.replace(
            "$ErrorActionPreference = 'Stop'",
            "$watch = [System.Diagnostics.Stopwatch]::StartNew(); [Console]::Error.WriteLine('TRACE host-start-ms ' + ([DateTimeOffset]::UtcNow).ToUnixTimeMilliseconds()); [Console]::Error.WriteLine('TRACE startup ' + $watch.Elapsed.TotalSeconds); $ErrorActionPreference = 'Stop'",
        ).replace(
            '    Import-Module "$PSHOME\\Modules\\CimCmdlets',
            "    [Console]::Error.WriteLine('TRACE utility-loaded ' + $watch.Elapsed.TotalSeconds)\n    Import-Module \"$PSHOME\\Modules\\CimCmdlets",
        ).replace(
            '    $sid =',
            "    [Console]::Error.WriteLine('TRACE cim-loaded ' + $watch.Elapsed.TotalSeconds)\n    $sid =",
        ).replace(
            '    $rows =',
            "    [Console]::Error.WriteLine('TRACE identity-loaded ' + $watch.Elapsed.TotalSeconds)\n    $rows =",
        ).replace(
            '        $owner =',
            "        [Console]::Error.WriteLine('TRACE querying-owner ' + $watch.Elapsed.TotalSeconds)\n        $owner =",
        ).replace(
            '        if ($owner.ReturnValue',
            "        [Console]::Error.WriteLine('TRACE owner-loaded ' + $watch.Elapsed.TotalSeconds)\n        if ($owner.ReturnValue",
        ).replace(
            '    [pscustomobject]@{ Complete',
            "    [Console]::Error.WriteLine('TRACE processes-loaded ' + $watch.Elapsed.TotalSeconds)\n    [pscustomobject]@{ Complete",
        ).replace(
            '        ConvertTo-Json -Depth 4 -Compress',
            "        ConvertTo-Json -Depth 4 -Compress\n    [Console]::Error.WriteLine('TRACE serialization-complete ' + $watch.Elapsed.TotalSeconds)\n    [Console]::Error.WriteLine('TRACE host-end-ms ' + ([DateTimeOffset]::UtcNow).ToUnixTimeMilliseconds())",
        )

        def traced_run(*args, **kwargs):
            start = time.monotonic()
            print("TRACE python-start-ms", time.time_ns() // 1_000_000, flush=True)
            kwargs["timeout"] = 45
            print("TRACE diagnostic-only deadline 45s; production remains 10s", flush=True)
            try:
                result = original_run(*args, **kwargs)
            except subprocess.SubprocessError as error:
                print("TRACE python-failure", type(error).__name__, round(time.monotonic() - start, 3), flush=True)
                stderr = error.stderr or b""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
                for line in stderr.splitlines():
                    if line.startswith("TRACE "):
                        print(line, flush=True)
                raise
            else:
                print("TRACE python-complete", round(time.monotonic() - start, 3), flush=True)
                print("TRACE python-end-ms", time.time_ns() // 1_000_000, flush=True)
                for line in result.stderr.splitlines():
                    if line.startswith("TRACE "):
                        print(line, flush=True)
                return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cd, "_WINDOWS_SCRIPT", script)
            patch.setattr(item.module, "_NATIVE_RUN", traced_run)
            yield
        return

    if item.name.startswith("test_native_posix_status_reads_harmless_node_arguments") and os.name == "posix":
        original_command = cd._read_linux_command
        original_executable = item.module._READ_LINUX_EXECUTABLE
        original_popen = item.module._NATIVE_POPEN

        def traced_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            if "node" in Path(args[0][0]).name.lower():
                print("TRACE fixture-node-pid", process.pid, flush=True)
            return process

        def traced_executable(pid):
            try:
                return original_executable(pid)
            except (OSError, ValueError) as error:
                print("TRACE linux-executable-failure", pid, type(error).__name__, flush=True)
                try:
                    state = (Path("/proc") / str(pid) / "stat").read_bytes().rsplit(b") ", 1)[1].split()[0].decode()
                    print("TRACE linux-executable-state", pid, state, flush=True)
                except (OSError, ValueError, IndexError) as observation:
                    print("TRACE linux-executable-observation", pid, type(observation).__name__, flush=True)
                raise

        def traced_command(pid):
            try:
                return original_command(pid)
            except (OSError, ValueError) as error:
                print("TRACE linux-command-failure", pid, type(error).__name__, flush=True)
                root = Path("/proc") / str(pid)
                for attempt in range(3):
                    try:
                        state = (root / "stat").read_bytes().rsplit(b") ", 1)[1].split()[0].decode()
                        raw = (root / "cmdline").read_bytes()
                        print("TRACE linux-state", attempt, state, len(raw), raw.endswith(b"\0"), flush=True)
                    except (OSError, ValueError, IndexError) as observation:
                        print("TRACE linux-observation-failure", attempt, type(observation).__name__, flush=True)
                raise

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cd, "_read_linux_command", traced_command)
            patch.setattr(item.module, "_READ_LINUX_EXECUTABLE", traced_executable)
            patch.setattr(item.module, "_NATIVE_POPEN", traced_popen)
            yield
        return

    yield
