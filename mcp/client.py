#!virtual_env/bin/python3
import asyncio

from fastmcp import Client


async def main():
    print('start')
    # Connect via stdio to a local script
    async with Client('server.py') as client:
        tools = await client.list_tools()
        print(f"Available tools: {tools}")

        # ping_tool
        print('=============ping_tool================')
        result = await client.call_tool("ping_tool")
        print(f"Result: {result}")

        # log_tool
        print('=============log_tool================')
        result = await client.call_tool("log_tool")
        print(f"Result: {result.data}")

        # build_tool
        result = await client.call_tool(
            "build",
            {
                "build_dir": "~/chromium-checkouts/chromium/src/out/Release/",
                'targets': ["url_unittests"],
            },
        )

    # Connect via SSE
    async with Client("http://localhost:8000/sse") as client:
        # ... use the client
        pass


if __name__ == '__main__':
    asyncio.run(main())
