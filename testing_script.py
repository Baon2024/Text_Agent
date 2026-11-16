

import asyncio
import json
import websockets


async def main():
    message = {
        "type": "new_text_agent",
        "issue_id": "test-issue-123",
        "driver_id": "test-driver-456",
        "driver_issue": "Driver has a flat tyre near the north entrance.",
        "opening_message": "Hi, are you available to help a driver with a flat tyre near the north entrance?"
    }

    async with websockets.connect("ws://localhost:3001") as ws:
        await ws.send(json.dumps(message))
        print("Sent new_text_agent message:")
        print(json.dumps(message, indent=2))


if __name__ == "__main__":
    asyncio.run(main())