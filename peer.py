import asyncio
import asyncssh
import sys
import socket
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog
from textual.containers import Vertical
from storage import init_db, save_messages, load_history
from discover import find_peers

LISTEN_PORT = 8022


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect("8.8.8.8", 80)
    ip = s.getsockname()[0]
    s.close()

    return ip


# --- SSH Server Side (Incoming Connections)...  --- #


class ChatSession(asyncssh.SSHServerSession):
    def __init__(self, app):
        self.app = app
        self._chan = None

    def connection_made(self, chan):
        self._chan = chan
        self._app.attach_incoming(chan)

    def shell_requested(self):
        return True

    def session_started(self):
        self._chan.write("")

    def data_received(self, data, datatype):
        self._app.on_peer_message(data)

    def eof_received(self):
        self._chan.close()


class ChatServer(asyncssh.SSHServer):
    def __init__(self,app):
        self._app = app

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def session_requested(self):
        return ChatSession(self._app)


# --- The Unified Peer Application --- #


class PeerApp(App):
    CSS = """
        RichLog { border: round $primary; padding: 0 1;}
        Input {dock: bottom;}
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(id="log", wrap=True, markup=True)
            yield Input(
                placeholder="Type a message or 'connect <ip>' to reach the peer..."
            )

    async def on_mount(self) -> None:
        self.db = init_db()
        self.log_widget = self.query_one("#log", RichLog)
        self.peer_id = "unknown"
        self.out_process = None  # for the outgoing message (if we connected)
        self.in_chan = None  # an incoming channel (if the are connected)

        my_ip = get_local_ip()
        self.log_widget.write(f"[dim]Your address: {my_ip}:{LISTEN_PORT}[/dim]")

        # 1. Start listening for incoming messgaes.
        await self.start_server()

        # 2. Try to auto-discover a peer on LAN.
        asyncio.create_task(self.auto_discover())

    async def start_server(self):
        try:
            await asyncssh.create_server(
                lambda: ChatServer(self),
                "",
                LISTEN_PORT,
                server_host_keys=["ssh_host_key"],
                authorized_client_keys="authorized_keys",
            )
            self.log_widget.write("[green]Listening for incoming peers.[/green]")
        except Exception as e:
            self.log_widget.write(f"[red]Could not start listener: {e}[/red]")
