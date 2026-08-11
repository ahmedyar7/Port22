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

    def begin_auth(self, username):
        return False

    def session_requested(self):
        return ChatSession()


async def main():
    await asyncssh.create_server(
        ChatServer,
        "127.0.0.1",
        8022,
        server_host_keys=["ssh_host_key"],
    )
    print("Server listening on 127.0.0.1:8022")
    await asyncio.Future()


asyncio.run(main())
