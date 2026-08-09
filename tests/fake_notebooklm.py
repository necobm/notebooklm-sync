#!/usr/bin/env python3
"""A stand-in for the upstream ``notebooklm`` binary.

The ``fake_cli`` fixture drops a wrapper for this script into a tmp dir and prepends
that dir to ``PATH``, so ``nlm.py``'s subprocess call resolves here instead of the
real CLI. Tests therefore need no network and no Google auth.

Behaviour is driven by a JSON scenario file at ``$FAKE_NLM_SCENARIO``:

    {
      "source list": {"sources": [...]},
      "source wait": {"stdout": {"status": "PROCESSING"}, "exit": 2}
    }

Keys match the leading subcommand words. Values are either a bare response object,
or an envelope of ``{"stdout": ..., "exit": N}`` when the exit code matters — which
it does for ``source wait`` (0 ready / 1 failed / 2 timeout).

Every invocation's argv is appended to ``$FAKE_NLM_CALLS`` so tests can assert on
the exact flags we passed.
"""

from __future__ import annotations

import json
import os
import sys

# Subcommands are matched longest-first so "source list" wins over "source".
KNOWN_COMMANDS = [
    "auth check",
    "source list",
    "source add",
    "source refresh",
    "source stale",
    "source wait",
    "list",
]

DEFAULTS: dict[str, object] = {
    "auth check": {"status": "ok", "checks": {"token_fetch": True}},
    "list": {"notebooks": []},
    "source list": {"sources": []},
    "source add": {"id": "new-source", "url": "", "status": "PROCESSING"},
    "source refresh": {"ok": True},
    "source stale": {"stale": False},
    "source wait": {"status": "READY"},
}


def command_of(argv: list[str]) -> str:
    """Return the scenario key for this invocation, ignoring flags and values.

    Also used by the ``fake_cli`` fixture to summarize recorded calls, so a test
    and the shim always agree on what a given argv "is".
    """
    words: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            # These flags take a value that must not be mistaken for a subcommand.
            if arg in ("-n", "--notebook", "--type", "--title", "--timeout", "--profile", "-p"):
                skip_next = True
            continue
        words.append(arg)

    joined = " ".join(words)
    for command in KNOWN_COMMANDS:
        if joined.startswith(command):
            return command
    return words[0] if words else ""


def main() -> int:
    argv = sys.argv[1:]

    calls_path = os.environ.get("FAKE_NLM_CALLS")
    if calls_path:
        with open(calls_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\n")

    scenario: dict = {}
    scenario_path = os.environ.get("FAKE_NLM_SCENARIO")
    if scenario_path and os.path.exists(scenario_path):
        with open(scenario_path, encoding="utf-8") as handle:
            scenario = json.load(handle)

    command = command_of(argv)
    response = scenario.get(command, DEFAULTS.get(command, {}))

    exit_code = 0
    if isinstance(response, dict) and "stdout" in response:
        exit_code = int(response.get("exit", 0))
        payload = response["stdout"]
    else:
        payload = response

    if isinstance(payload, str):
        sys.stdout.write(payload)
    else:
        sys.stdout.write(json.dumps(payload))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
