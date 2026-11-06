"""Exercise the real Qt serial event loop against a local pseudo-terminal."""

from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QCoreApplication, QTimer

from lighting_controller.serial_transport import SerialTransport


class SerialTransportTests(unittest.TestCase):
    def test_handshake_and_status_over_pseudo_terminal(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        master, slave = os.openpty()
        os.set_blocking(master, False)
        received = bytearray()
        replies = []
        errors = []
        transport = SerialTransport()
        transport.error_message.connect(errors.append)

        def emulate_uno() -> None:
            try:
                received.extend(os.read(master, 4096))
            except BlockingIOError:
                return
            while b"\n" in received:
                command, _, rest = received.partition(b"\n")
                received[:] = rest
                verb = command.decode("ascii").split()[0]
                if verb == "HELLO":
                    os.write(master, b"OK HELLO 1 32\n")
                elif verb == "STATUS":
                    os.write(master, b"OK STATUS IDLE -1 0 0\n")

        def connected(ready: bool) -> None:
            if ready:
                transport.send("STATUS", lambda reply: (replies.append(reply), app.quit()))

        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(emulate_uno)
        poll.start()
        transport.ready_changed.connect(connected)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(app.quit)
        deadline.start(5_000)
        try:
            transport.open_port(os.ttyname(slave))
            app.exec()
            self.assertEqual(errors, [])
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].command, "STATUS")
        finally:
            deadline.stop()
            poll.stop()
            transport.close_port()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
