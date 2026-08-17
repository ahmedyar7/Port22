import asyncio, asyncssh, sys
import socket
from zeroconf import ServiceInfo, Zeroconf


def get_local_ip():
    """"""

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # This doesn't sends the data it just picks the right interface.
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()

    return ip


def advertise(port=8022):

    ip = get_local_ip()

    info = ServiceInfo(
        "_port22._tcp.local",
        f"{socket.gethostname()}._port22._tcp.local",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"app": "port22"},
    )

    zc = Zeroconf()
    zc.register_service(info)
    print(f"Advertising Port22 on {ip}: {port}")

    return zc, info 


class ChatSession(asyncssh.SSHServerSession):
    def __init__(self):
        self._chan = None

    def connection_made(self, chan):
        self._chan = chan

    def shell_requested(self):
        return True

    def session_started(self):
        self._chan.write("Connected! Start typing.\n")
        asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        loop = asyncio.get_event_loop()
        while not self._chan.is_closing():
            msg = await loop.run_in_executor(None, sys.stdin.readline)
            if not msg:
                break
            self._chan.write(f"[peer] {msg}")

    def data_received(self, data, datatype):
        print(f"[peer] {data}", end="")

    def eof_received(self):
        self._chan.close()


class ChatServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def session_requested(self):
        return ChatSession()


async def main():
    await asyncssh.create_server(
        ChatServer,
        "127.0.0.1",
        8022,
        server_host_keys=["ssh_host_key"],
        authorized_client_keys="authorized_keys",
    )
    print("Server listening on 127.0.0.1:8022")
    await asyncio.Future()


asyncio.run(main())
