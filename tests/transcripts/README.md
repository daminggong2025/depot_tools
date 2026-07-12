# CLI transcript tests

Each `*.txt` file in this directory pins the command line contract (output
and exit code) of a depot_tools script. `tests/cli_transcript_test.py`
replays them against the scripts of this checkout, never against a
PATH-installed depot_tools.

Format:

- A line starting with `$ ` is a command. `$ gclient.py help` runs
  `<checkout>/vpython3 <checkout>/gclient.py help` in an empty temporary
  directory, with stdout and stderr merged.
- The following lines are the expected output. A line containing only `...`
  matches any number of output lines, including zero.
- Every command must end with an `[exit: N]` line asserting its exit code.
- Comment lines starting with `#` are allowed before the first command.
- A first line `# known-bug: <description>` records the desired contract for
  a known bug. Such a transcript must currently fail; the test flags it once
  it starts passing, as a reminder to remove the marker.

Output is normalized before comparison: `\r\n` becomes `\n`, ANSI escapes
are stripped, trailing whitespace and trailing blank lines are dropped, and
the checkout path is replaced with `<DEPOT_TOOLS>`.

To update expectations after an intended behavior change, run:

    CLI_TRANSCRIPT_UPDATE=1 ./vpython3 tests/cli_transcript_test.py

This rewrites failing transcripts with the actual output as literal lines;
re-add `...` wildcards by hand where the output is variable. Known-bug
transcripts are never rewritten.
