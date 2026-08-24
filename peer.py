"""
Port22 -- decentralised peer-to-peer chat.

Every device runs this one program: it listens for an incoming SSH connection
*and* can dial out to another peer, both inside a single asyncio event loop.
SSH (asyncssh) is the entire transport and security layer; message history is
kept locally in SQLite; the UI is a Textual TUI.

Connection strategy: mDNS first, manual `connect <ip>` as the fallback.
Exactly one chat channel is active at a time -- either `self.out_process`
(we dialled them) or `self.in_chan` (they dialled us).
"""

import asyncio
import contextlib

import asyncssh
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog

from discover import (
    advertise,
    close_zeroconf,
    find_peers,
    get_local_ip,
    open_zeroconf,
    service_instance_name,
    unadvertise,
)
from storage import init_db, load_history, save_messages

LISTEN_PORT = 8022
USERNAME = "me"
HOST_KEY = "ssh_host_key"
CLIENT_KEY = "client_key"
AUTHORIZED_KEYS = "authorized_keys"

DISCOVERY_TIMEOUT = 3.0   # seconds spent browsing per pass
DISCOVERY_INTERVAL = 5.0  # pause between passes while still unconnected
DIAL_GRACE = 4.0          # how long the higher-IP peer waits before dialling


# --- SSH Server Side (Incoming Connections) --- #


class ChatSession(asyncssh.SSHServerSession):
    """One inbound chat channel: a peer dialled us."""

    def __init__(self, app: "PeerApp"):
        self._app = app
        self._chan = None
        self._buf = ""
        self._accepted = False

    def connection_made(self, chan):
        self._chan = chan

    def shell_requested(self):
        return True

    def session_started(self):
        # If the peer did allocate a pty, asyncssh would run its line editor
        # and echo everything back -- which the other end would then store as
        # an incoming message. Turn both off; this channel carries raw lines.
        try:
            self._chan.set_line_mode(False)
            self._chan.set_echo(False)
        except Exception:
            pass

        # Only one active channel at a time -- turn the second one away.
        self._accepted = self._app.attach_incoming(self._chan)
        if not self._accepted:
            self._chan.write("Peer is busy with another chat.\n")
            self._chan.close()

    def data_received(self, data, datatype):
        if not self._accepted:
            return
        # Data arrives in arbitrary chunks, so reassemble whole lines.
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._app.on_peer_message(line)

    def eof_received(self):
        self._chan.close()
        return False

    def connection_lost(self, exc):
        if self._accepted:
            self._app.detach_incoming(self._chan)


class ChatServer(asyncssh.SSHServer):
    def __init__(self, app: "PeerApp"):
        self._app = app

    def begin_auth(self, username):
        return True  # keep going: the public-key check still applies

    def public_key_auth_supported(self):
        return True

    def session_requested(self):
        return ChatSession(self._app)


# --- The Unified Peer Application --- #


class PeerApp(App):
    CSS = """
        RichLog { border: round $primary; padding: 0 1; }
        Input { dock: bottom; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(id="log", wrap=True, markup=True)
            yield Input(
                placeholder="Type a message, or 'connect <ip>' to reach the peer..."
            )

    async def on_mount(self) -> None:
        self.db = init_db()
        self.log_widget = self.query_one("#log", RichLog)

        self.peer_id = None            # ip of whoever we are talking to
        self.out_process = None        # set when WE dialled THEM
        self.out_conn = None
        self.in_chan = None            # set when THEY dialled US
        self.ssh_server = None
        self.reader_task = None
        self.discover_task = None

        self.azc = None                # the one Zeroconf instance for this run
        self.service_info = None       # our own advertisement
        self.my_ip = get_local_ip()
        self.my_service_name = service_instance_name(LISTEN_PORT)
        self._shut_down = False

        self.log_widget.write(f"[dim]Your address: {self.my_ip}:{LISTEN_PORT}[/dim]")

        # 1. Start listening for incoming peers.
        await self.start_server()

        # 2. Announce ourselves on the LAN -- only once the listener is up, so
        #    nobody dials an address that is not accepting connections yet.
        await self.start_advertising()

        # 3. Look for somebody to talk to.
        self.discover_task = asyncio.create_task(self.auto_discover())

    # --- listener + advertisement --- #

    async def start_server(self) -> None:
        try:
            self.ssh_server = await asyncssh.create_server(
                lambda: ChatServer(self),
                "",  # all interfaces -- 127.0.0.1 is unreachable from the LAN
                LISTEN_PORT,
                server_host_keys=[HOST_KEY],
                authorized_client_keys=AUTHORIZED_KEYS,
            )
            self.log_widget.write("[green]Listening for incoming peers.[/green]")
        except Exception as e:
            self.ssh_server = None
            self.log_widget.write(f"[red]Could not start listener: {e}[/red]")

    async def start_advertising(self) -> None:
        try:
            self.azc = await open_zeroconf()
            self.service_info = await advertise(self.azc, LISTEN_PORT, self.my_ip)
            self.log_widget.write(
                f"[green]Advertising as[/green] [b]{self.my_service_name}[/b] "
                f"[dim]({self.my_ip}:{LISTEN_PORT})[/dim]"
            )
        except Exception as e:
            self.log_widget.write(f"[red]mDNS advertising failed: {e}[/red]")

    # --- discovery --- #

    async def auto_discover(self) -> None:
        """Browse for a peer until we have a channel, then stop.

        Keeps retrying so the two devices do not have to be started at the same
        moment; the user can always cut in with `connect <ip>`.
        """
        if self.azc is None:
            self.log_widget.write(
                "[yellow]No mDNS -- use 'connect <ip>' to reach the peer.[/yellow]"
            )
            return

        self.log_widget.write("[dim]Searching for peers on the LAN...[/dim]")
        announced_empty = False

        while not self.has_channel() and not self._shut_down:
            try:
                peers = await find_peers(
                    self.azc,
                    timeout=DISCOVERY_TIMEOUT,
                    exclude_names={self.my_service_name},
                    exclude_ips={self.my_ip},
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log_widget.write(f"[red]Discovery failed: {e}[/red]")
                return

            if self.has_channel():
                return

            if not peers:
                if not announced_empty:
                    self.log_widget.write(
                        "[yellow]No peers found yet -- still looking. "
                        "You can also type 'connect <ip>'.[/yellow]"
                    )
                    announced_empty = True
                await asyncio.sleep(DISCOVERY_INTERVAL)
                continue

            name, ip, port = peers[0]
            self.log_widget.write(f"[green]Found peer:[/green] {name} at {ip}:{port}")

            # Both devices discover each other at the same time. With a single
            # active channel, let the lower IP do the dialling; the other side
            # waits briefly and only dials if that call never arrives.
            if _ip_key(self.my_ip) > _ip_key(ip):
                await asyncio.sleep(DIAL_GRACE)
                if self.has_channel():
                    return

            await self.connect_to(ip, port)
            if not self.has_channel():
                await asyncio.sleep(DISCOVERY_INTERVAL)

    # --- outgoing connection --- #

    async def connect_to(self, ip: str, port: int = LISTEN_PORT) -> None:
        if self.has_channel():
            self.log_widget.write("[yellow]Already in a chat -- ignoring.[/yellow]")
            return

        self.log_widget.write(f"[dim]Connecting to {ip}:{port}...[/dim]")
        try:
            conn = await asyncssh.connect(
                ip,
                port=port,
                known_hosts=None,
                username=USERNAME,
                client_keys=[CLIENT_KEY],
            )
            # No term_type: a pty would bring asyncssh's line editor (and its
            # echo) along with it. We just want a plain bidirectional pipe.
            process = await conn.create_process()
        except Exception as e:
            self.log_widget.write(f"[red]Connection to {ip} failed: {e}[/red]")
            return

        self.out_conn = conn
        self.out_process = process
        self.set_peer(ip)
        self.log_widget.write(f"[green]Connected to {ip}:{port}.[/green]")
        self.reader_task = asyncio.create_task(self.read_outgoing())

    async def read_outgoing(self) -> None:
        """Pump the lines coming back over the channel we dialled."""
        try:
            async for line in self.out_process.stdout:
                line = line.strip()
                if line:
                    self.on_peer_message(line)
        except (asyncio.CancelledError, asyncssh.Error, OSError):
            pass
        finally:
            if not self._shut_down:
                self.log_widget.write("[yellow]Peer disconnected.[/yellow]")
                await self.close_outgoing()

    async def close_outgoing(self) -> None:
        process, conn = self.out_process, self.out_conn
        self.out_process = self.out_conn = None
        if process is not None:
            with contextlib.suppress(Exception):
                process.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    # --- incoming connection --- #

    def attach_incoming(self, chan) -> bool:
        """Accept an inbound channel unless one is already active."""
        if self.has_channel():
            return False
        self.in_chan = chan
        peername = chan.get_extra_info("peername")
        ip = peername[0] if peername else "unknown"
        self.set_peer(ip)
        self.log_widget.write(f"[green]Peer {ip} connected to us.[/green]")
        return True

    def detach_incoming(self, chan) -> None:
        if self.in_chan is chan:
            self.in_chan = None
            if not self._shut_down:
                self.log_widget.write("[yellow]Peer disconnected.[/yellow]")

    # --- shared chat plumbing --- #

    def has_channel(self) -> bool:
        return self.out_process is not None or self.in_chan is not None

    def set_peer(self, ip: str) -> None:
        """Remember who we are talking to and replay their history once."""
        if self.peer_id == ip:
            return
        self.peer_id = ip
        rows = load_history(self.db, ip)
        if rows:
            self.log_widget.write(f"[dim]-- history with {ip} --[/dim]")
            for direction, body, ts in rows:
                tag = "You" if direction == "sent" else "Peer"
                self.log_widget.write(f"[dim]{ts[11:16]}[/dim] [b]{tag}:[/b] {body}")
            self.log_widget.write("[dim]-- end of history --[/dim]")

    def on_peer_message(self, line: str) -> None:
        self.log_widget.write(f"[b]Peer:[/b] {line}")
        save_messages(self.db, self.peer_id or "unknown", "recv", line)

    def send_line(self, msg: str) -> bool:
        """Write to whichever channel we happen to have."""
        try:
            if self.out_process is not None:
                self.out_process.stdin.write(msg + "\n")
            elif self.in_chan is not None:
                self.in_chan.write(msg + "\n")
            else:
                return False
        except Exception as e:
            self.log_widget.write(f"[red]Send failed: {e}[/red]")
            return False
        return True

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        event.input.clear()
        if not msg:
            return

        lowered = msg.lower()
        if lowered.startswith("connect "):
            parts = msg.split()
            ip = parts[1]
            port = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else LISTEN_PORT
            await self.connect_to(ip, port)
            return
        if lowered in ("/quit", "/exit"):
            self.exit()
            return

        if not self.has_channel():
            self.log_widget.write(
                "[yellow]No peer connected yet -- try 'connect <ip>'.[/yellow]"
            )
            return

        if self.send_line(msg):
            self.log_widget.write(f"[b]You:[/b] {msg}")
            save_messages(self.db, self.peer_id or "unknown", "sent", msg)

    # --- shutdown --- #

    async def on_unmount(self) -> None:
        await self.shutdown()

    async def shutdown(self) -> None:
        # Default True so an app that never finished on_mount (and therefore
        # has nothing to tear down) simply drops out here.
        if getattr(self, "_shut_down", True):
            return
        self._shut_down = True

        if self.discover_task is not None:
            self.discover_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.discover_task
        if self.reader_task is not None:
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader_task

        # Withdraw the advertisement first, then tear the Zeroconf down, so
        # peers stop seeing an address that no longer answers.
        await unadvertise(self.azc, self.service_info)
        await close_zeroconf(self.azc)
        self.azc = self.service_info = None

        await self.close_outgoing()
        if self.in_chan is not None:
            with contextlib.suppress(Exception):
                self.in_chan.close()
            self.in_chan = None
        if self.ssh_server is not None:
            with contextlib.suppress(Exception):
                self.ssh_server.close()
            self.ssh_server = None
        with contextlib.suppress(Exception):
            self.db.close()


def _ip_key(ip: str) -> tuple:
    """Sort IPv4 numerically so 10.0.0.9 comes before 10.0.0.10."""
    try:
        return tuple(int(part) for part in ip.split("."))
    except ValueError:
        return (999, 999, 999, 999)


if __name__ == "__main__":
    PeerApp().run()
