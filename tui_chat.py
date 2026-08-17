import asyncio
import asyncssh
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog
from textual.containers import (
    Vertical,
)  # this means that message would be stacked on top of each other
from storage import init_db, save_messages, load_history

PEER = "127.0.0.1"


class ChatApp(App):

    # This is the CSS configuration.

    CSS = """
    RichLog { border: round $primary; padding: 0 1; }
    Input { dock: bottom; }
    """

    def compose(self) -> ComposeResult:
        """
        This function is responsible for building the layout
        of the application.
        """
        with Vertical():
            yield RichLog(id="log", wrap=True, markup=True)
            yield Input(placeholder="Type a message and press Enter...")

    async def on_mount(self) -> None:
        """
        This function would load the past messages
        upon mounting of the database and load_history()
        function.
        """
        self.db = init_db()
        self.log_widget = self.query_one("#log", RichLog)

        # show past history
        for direction, body, ts in load_history(self.db, PEER):
            tag = "You" if direction == "sent" else "Peer"
            self.log_widget.write(
                f"[dim]{ts[11:16]}[/dim] [b]{tag}:[/b] {body.strip()}"
            )

        # connect to the peer in the background
        self.connect_task = asyncio.create_task(self.connect())

    async def connect(self) -> None:
        """
        This function is responsible for connecting to the different
        froms of SSH clients that are present.
        """
        try:
            self.conn = await asyncssh.connect(
                PEER,
                port=8022,
                known_hosts=None,
                username="me",
                client_keys=["client_key"],
            )

            self.process = await self.conn.create_process(term_type="ansi")
            self.log_widget.write("[green]Connected to peer.[/green]")

            # start listening for incoming messages
            asyncio.create_task(self.read_output())

        except Exception as e:
            self.log_widget.write(f"[red]Connection failed: {e}[/red]")

    async def read_output(self) -> None:
        """
        This function is responsible for creating the output of the
        function and actually storing them inside of the chat.db.
        """

        async for line in self.process.stdout:
            line = line.rstrip("\n")

            if line:
                self.log_widget.write(f"[b]Peer:[/b] {line}")
                save_messages(self.db, PEER, "recv", line)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()

        if not msg:
            return

        event.input.clear()
        self.log_widget.write(f"[b]You:[/b] {msg}")

        save_messages(self.db, PEER, "sent", msg)

        if hasattr(self, "process"):
            self.process.stdin.write(msg + "\n")


if __name__ == "__main__":
    ChatApp().run()
