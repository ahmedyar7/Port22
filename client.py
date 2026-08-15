import asyncio
import asyncssh
import sys
from storage import init_db, save_messages, load_history

PEER = "127.0.0.1"


async def main():
    db = init_db()

    for direction, body, ts in load_history(db, PEER):
        if direction == "sent":
            tag = "You"
        else:
            tag = "Peer"

        print(f"{ts[11:16]} {tag}:{body}")

    async with asyncssh.connect(
        PEER, port=8022, known_hosts=None, username="me", client_keys=["client_key"]
    ) as conn:
        async with conn.create_process(term_type="ansi") as process:

            async def read_output():
                async for line in process.stdout:
                    # print(line, end="")
                    save_messages(db, PEER, "recv", line)

            asyncio.create_task(read_output())

            loop = asyncio.get_event_loop()
            while True:
                msg = await loop.run_in_executor(None, sys.stdin.readline)
                if not msg:
                    break

                process.stdin.write(msg)
                save_messages(db, PEER, "sent", msg)


asyncio.run(main())
