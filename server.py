import asyncio, asyncssh, logging, sys

# logging.basicConfig(level=logging.DEBUG)


class ChatSession(asyncssh.SSHServerSession):
    def connection_made(self, chan):
        self._chan = chan

    def shell_requested(self):
        return True

    def session_started(self):
        self._chan.write("Connected to peer!\n")

    def data_received(self, data, datatype):
        print(f"Peer says: {data}", end="")

    def eof_received(self):
        self._chan.close()


class ChatServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        print("Connection incoming...")

    def connection_lost(self, exc):
        if exc:
            print(f"Connection lost: {exc}")

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
    print("Server listening on localhost:8022")
    await asyncio.Future()


asyncio.run(main())
