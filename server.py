import asyncio, asyncssh, logging, sys

# logging.basicConfig(level=logging.DEBUG)


class ChatSession(asyncssh.SSHServerSession):

    def __init__(self):
        self._chan = None

    def connection_made(self, chan):
        self._chan = chan

    def shell_requested(self):
        return True

    def session_started(self):
        self._chan.write("Connected to peer!\n")

        # Create a task that would send server operator's typed input to the peers.
        asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        loop = asyncio.get_event_loop()

        while not self._chan.is_closing():
            msg = await loop.run_in_executor(None, sys.stdin.readline)

            if not msg:
                break

            self._chan.write(f"[peer]:{msg}")

    def data_received(self, data, datatype):
        print(f"[peer] {data}", end="")

    def eof_received(self):
        self._chan.close()


class ChatServer(asyncssh.SSHServer):

    def connection_made(self, conn):
        self._conn = conn

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True  # This means that now the authentication is required.

    def session_requested(self):
        return ChatSession()


async def main():

    async with asyncssh.connect(
        "127.0.0.1",
        port=8022,
        known_host=None,
        username="me",
        client_keys=["client_key"],  #
    ) as conn:

        async with conn.create_process(term_type="ansi") as process:

            async def read_output():
                async for line in process.stdout:
                    print(line, end="")

                asyncio.create_task(read_output())

                loop = asyncio.get_event_loop()

                while True:
                    msg = await loop.run_in_executor(None, sys.stdin.readline)

                    if not msg:
                        break

                    process.stdin.write(msg)


asyncio.run(main())
