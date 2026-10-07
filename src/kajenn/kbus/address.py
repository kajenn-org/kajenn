# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""KajennBus addresses: ``uds:<path>`` or ``tcp:<ip>:<port>``.

A UDS listener binds without stealing an existing pathname and unlinks only
the socket it created; a TCP address is loopback unless the listener opts in
to a network address. A client connects outside the machine only when it opts
in too, which it does when it holds the network listener's secret.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import stat
import sys
from typing import Any

__all__ = ["KBusAddress"]


class KBusAddress:
    """A configured address; non-loopback binding requires explicit listener opt-in."""

    def __init__(
        self,
        address: str,
        *,
        allow_network_listener: bool = False,
        allow_network_client: bool = False,
    ) -> None:
        """Parse one address into the form it will be opened with.

        Args:
            address: ``uds:<path>`` or ``tcp:<loopback-ip>:<port>``.
            allow_network_listener: admit a non-loopback IP, for a listener an
                operator placed on a private network.
            allow_network_client: admit a non-loopback destination, for a
                client presenting the secret of a network listener.

        Raises:
            ValueError: the address is neither form, its TCP part does not
                name an IP and a port, the port is outside 0–65535, or the IP
                is not loopback and no opt-in was given.
        """
        self.address = address
        transport, _, location = address.partition(":")
        self.path = None
        self._socket_identity: tuple[int, int] | None = None
        self.host = None
        self.port = None
        self.allow_network_client = allow_network_client
        if transport == "uds" and location:
            self.path = location
        elif transport == "tcp":
            host, _, port = location.rpartition(":")
            try:
                valid = ipaddress.ip_address(host).is_loopback
                port_number = int(port)
            except ValueError:
                raise ValueError("TCP address must name a loopback IP and port") from None
            network = allow_network_listener or allow_network_client
            if (not valid and not network) or not 0 <= port_number <= 65535:
                raise ValueError("TCP proof requires a loopback IP and valid port")
            self.host, self.port = host, port_number
        else:
            raise ValueError("expected uds:<path> or tcp:<loopback-ip>:<port>")

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a client stream to this address.

        Raises:
            ValueError: the destination is a TCP address that is not loopback
                and the client did not opt in to a network destination.
        """
        if self.path is not None:
            return await asyncio.open_unix_connection(self.path)
        if not self.allow_network_client and not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("clients require a loopback destination")
        return await asyncio.open_connection(self.host, self.port)

    async def listen(self, callback: Any) -> asyncio.Server:
        """Bind a server on this address and serve it with ``callback``.

        A UDS listener binds its own socket and never unlinks a pathname it
        did not create, so another runner's socket is refused instead of
        stolen; the pathname it did create is remembered for
        ``unlink_owned_socket``.

        Raises:
            RuntimeError: this address already owns a bound socket.
            FileExistsError: the pathname already names a directory entry.
            OSError: the pathname was replaced between the bind and the check.
        """
        if self.path is not None:
            if self._socket_identity is not None:
                raise RuntimeError("address already owns a bound socket")
            # Some platforms allow bind through a dangling symlink. Refuse
            # every existing directory entry, not just an existing target.
            try:
                os.lstat(self.path)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(f"socket path already exists: {self.path}")
            # bind() atomically refuses another runner's socket. Passing path= to
            # asyncio would first unlink an existing socket, stealing its name.
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.setblocking(False)
                sock.bind(self.path)
                bound = os.lstat(self.path)
                if not stat.S_ISSOCK(bound.st_mode):
                    raise OSError("bound socket pathname was replaced")
                self._socket_identity = (bound.st_dev, bound.st_ino)
                options: dict[str, Any] = {"cleanup_socket": False} if sys.version_info >= (3, 13) else {}
                return await asyncio.start_unix_server(callback, sock=sock, **options)
            except BaseException:
                sock.close()
                self.unlink_owned_socket()
                raise
        return await asyncio.start_server(callback, host=self.host, port=self.port)

    @property
    def network(self) -> bool:
        """Whether this is a TCP address outside the loopback range."""
        return self.host is not None and not ipaddress.ip_address(self.host).is_loopback

    def unlink_owned_socket(self) -> None:
        """Remove only the pathname still naming this listener's socket.

        Call after closing the listener. A replaced pathname belongs to its new
        owner. Parent directories must be private/operator-controlled; this is
        lifecycle ownership, not protection against a hostile filesystem writer.
        """
        identity, self._socket_identity = self._socket_identity, None
        if self.path is None or identity is None:
            return
        try:
            current = os.lstat(self.path)
            if (current.st_dev, current.st_ino) == identity:
                os.unlink(self.path)
        except FileNotFoundError:
            pass
