You are an expert at python and writing model context protocol (mcp) tools with fast mcp version 1.9.4.
This folder is the current set of tools made available. New tools should be written in a similar style as existing files.
The first thing you should do to write a new tools is read the existing files in this folder to understand the mcp tools
already available. New tools should be include in @server.py

These tools will be written to run using vpython3. This is not the same command as python3 which should not be used to run
tools nor tests in this folder. vpython3 can be invoked like `vpython build_test.py`.

Tools should also be instrumented with the telemetry library in @../infra_lib/telemetry. This library can be made importable
by including it in the sys.path like:

sys.path.insert(
    0,
    os.path.abspath(
        pathlib.Path(__file__).resolve().parent.parent.joinpath(
            pathlib.Path('infra_lib'))))


And then imported with `import telemetry`.