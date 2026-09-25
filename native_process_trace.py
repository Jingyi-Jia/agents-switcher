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
            "$watch = [System.Diagnostics.Stopwatch]::StartNew(); [Console]::Error.WriteLine('TRACE startup ' + $watch.Elapsed.TotalSeconds); $ErrorActionPreference = 'Stop'",
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
        )

        def traced_run(*args, **kwargs):
            start = time.monotonic()
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
            yield
        return

    yield
