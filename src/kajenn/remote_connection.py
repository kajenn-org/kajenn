# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Bounded request/reply connections for operator-configured UDS or loopback TCP.

One connection is one stream at a time, and the calls parked on it belong to
that stream: when the link is lost they all fail, and none is replayed — the
caller learns whether its frame was sent (``outcome`` on the failure) and
decides for itself. Reconnecting opens a new stream, which admits new calls
only. ``max_calls`` bounds how many calls are in flight, and with them the
bytes both ends retain.

Being connected grants no authority over the peer's process: this module
opens, calls and closes, and stopping a process is the caller's own business.
"""

import asyncio
import contextlib
import ipaddress
import os
import socket
import stat
import sys
from typing import Any

from .channel.frame import Frame, FrameStream


class RemoteCallFailed(ConnectionError):
    """A transport failure with an explicit possible-delivery outcome."""

    def __init__(self, reason: str, *, outcome: str) -> None:
        super().__init__(reason)
        self.outcome = outcome


class RemotePeerMismatch(RemoteCallFailed):
    """The peer is not the runner launched by this mount; no application call sent."""

    def __init__(self) -> None:
        super().__init__("remote peer does not match the owned runner", outcome="not_sent")


class RemoteCallCancelled(asyncio.CancelledError):
    """Cancellation stops waiting; a possibly delivered call may still execute."""

    def __init__(self, *, outcome: str) -> None:
        super().__init__("remote call cancelled")
        self.outcome = outcome


class RemoteAddress:
    """A configured address; non-loopback binding requires explicit listener opt-in."""

    def __init__(self, address: str, *, allow_network_listener: bool = False) -> None:
        """Parse one address into the form it will be opened with.

        Args:
            address: ``uds:<path>`` or ``tcp:<loopback-ip>:<port>``.
            allow_network_listener: admit a non-loopback IP, for a listener an
                operator placed on a private network.

        Raises:
            ValueError: the address is neither form, its TCP part does not
                name an IP and a port, or that IP is not loopback and no
                listener opt-in was given.
        """
        self.address = address
        transport, _, location = address.partition(":")
        self.path = None
        self._socket_identity: tuple[int, int] | None = None
        self.host = None
        self.port = None
        if transport == "uds" and location:
            self.path = location
        elif transport == "tcp":
            host, _, port = location.rpartition(":")
            try:
                valid = ipaddress.ip_address(host).is_loopback
                port_number = int(port)
            except ValueError:
                raise ValueError("TCP address must name a loopback IP and port") from None
            if (not valid and not allow_network_listener) or not 0 <= port_number <= 65535:
                raise ValueError("remote TCP proof requires a loopback IP and valid port")
            self.host, self.port = host, port_number
        else:
            raise ValueError("expected uds:<path> or tcp:<loopback-ip>:<port>")

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a client stream to this address.

        Raises:
            ValueError: the destination is a TCP address that is not loopback
                — a client never leaves the machine, whatever a listener may.
        """
        if self.path is not None:
            return await asyncio.open_unix_connection(self.path)
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("remote clients require a loopback destination")
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
                raise FileExistsError(f"remote socket path already exists: {self.path}")
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


class RemoteConnection:
    """One full-duplex frame connection with bounded calls and no replay."""

    def __init__(self, address: str, *, timeout: float = 30.0, max_calls: int = 16,
                 expected_instance_id: str | None = None) -> None:
        """Build a connection, without opening it.

        Args:
            address: ``uds:<path>`` or ``tcp:<loopback-ip>:<port>``.
            timeout: seconds one call may take, from admission to reply.
            max_calls: how many calls may be in flight at once.
            expected_instance_id: when given, the peer must present this
                launch identity on the first ``/_ready`` of every stream, or
                the stream is dropped with ``RemotePeerMismatch``.

        Raises:
            ValueError: ``timeout`` is not positive, ``max_calls`` is below 1,
                or the address is not one of the two forms.
        """
        if timeout <= 0 or max_calls < 1:
            raise ValueError("timeout and max_calls must be positive")
        self.address = RemoteAddress(address)
        self.expected_instance_id = expected_instance_id
        self.timeout = timeout
        self._slots = asyncio.Semaphore(max_calls)
        self._connect_lock = asyncio.Lock()
        self._stream: FrameStream | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, tuple[FrameStream, asyncio.Future[Frame], str]] = {}
        self._abandoned: dict[str, tuple[FrameStream, str]] = {}
        self._closed = False

    async def call(self, frame: Frame) -> Frame:
        """Send one frame and wait for the reply correlated with it.

        The call takes an admission slot, opens the stream if none is open,
        parks itself under the frame's id and writes. A reply is accepted only
        from the stream that carried the call and only on the same path.

        Returns:
            The ``REPLY`` frame.

        Raises:
            ValueError: the frame's method is not ``CALL``, or its id is
                already in flight.
            RemotePeerMismatch: the peer is not the runner this connection
                expects; nothing was sent.
            RemoteCallFailed: the link failed or the call timed out;
                ``outcome`` is ``not_sent`` or ``unknown``, and a call is
                never replayed.
            RemoteCallCancelled: the caller was cancelled; ``outcome`` says
                whether the frame had already gone out.
        """
        if frame.method != "CALL":
            raise ValueError("remote request/reply calls require method CALL")
        sent = False
        stream: FrameStream | None = None
        future: asyncio.Future[Frame] | None = None
        try:
            async with asyncio.timeout(self.timeout):
                async with self._slots:
                    stream = await self._connect()
                    if frame.id in self._pending or frame.id in self._abandoned:
                        raise ValueError("duplicate in-flight correlation id")
                    future = asyncio.get_running_loop().create_future()
                    self._pending[frame.id] = (stream, future, frame.path)
                    try:
                        # The write may succeed before drain fails: never replay.
                        sent = True
                        await stream.write(frame)
                        return await asyncio.shield(future)
                    finally:
                        self._pending.pop(frame.id, None)
        except asyncio.CancelledError as exc:
            if sent and stream is not None and future is not None and not future.done():
                await self._abandon(frame, stream)
            raise RemoteCallCancelled(outcome="unknown" if sent else "not_sent") from exc
        except RemoteCallFailed:
            raise
        except (OSError, TimeoutError) as exc:
            if sent and stream is not None and future is not None and not future.done():
                await self._abandon(frame, stream)
            raise RemoteCallFailed(str(exc), outcome="unknown" if sent else "not_sent") from exc
        finally:
            if future is not None and not future.done():
                future.cancel()

    async def _abandon(self, frame: Frame, stream: FrameStream) -> None:
        """Remember a call nobody waits for, so its reply is read and dropped.

        Past 256 abandoned calls the stream is closed instead: the peer is not
        answering, and the bookkeeping stops growing.
        """
        if len(self._abandoned) >= 256:
            await stream.close()
            return
        self._abandoned[frame.id] = (stream, frame.path)

    async def _connect(self) -> FrameStream:
        """The open stream, opening one under the lock when there is none.

        A new stream is probed with ``/_ready`` when an instance id is
        expected, and the reader task that dispatches replies is started.

        Raises:
            ConnectionError: this connection is closed.
            RemotePeerMismatch: the peer did not present the expected identity.
        """
        async with self._connect_lock:
            if self._closed:
                raise ConnectionError("remote connection is closed")
            if self._stream is None:
                reader, writer = await self.address.connect()
                stream = FrameStream(reader, writer)
                try:
                    if self.expected_instance_id is not None:
                        # Do not disclose the expected identity in the request:
                        # a foreign runner must report its own launch identity.
                        probe = Frame(method="CALL", path="/_ready")
                        await stream.write(probe)
                        reply = await stream.read()
                        if (reply is None or reply.method != "REPLY" or reply.id != probe.id
                                or reply.path != probe.path or reply.info.get("ready") is not True
                                or reply.info.get("instance_id") != self.expected_instance_id):
                            raise RemotePeerMismatch()
                except BaseException:
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await stream.close()
                    raise
                self._stream = stream
                self._reader_task = asyncio.create_task(self._read_replies(stream))
            return self._stream

    async def _read_replies(self, stream: FrameStream) -> None:
        """Read replies off one stream and hand each to the call waiting for it.

        A reply for an abandoned call is dropped. A reply that belongs to
        another stream or another path ends the stream, and so does a frame
        that is not a ``REPLY``. When the loop ends, every call still parked on
        this stream fails with ``outcome="unknown"``.
        """
        reason = "remote connection ended"
        try:
            while (frame := await stream.read()) is not None:
                if frame.method != "REPLY":
                    raise ValueError("remote peer sent a non-reply frame")
                abandoned = self._abandoned.get(frame.id)
                if abandoned is not None:
                    owner, path = abandoned
                    if owner is not stream or frame.path != path:
                        raise ValueError("abandoned reply does not belong to this connection and route")
                    self._abandoned.pop(frame.id, None)
                    continue
                parked = self._pending.get(frame.id)
                if parked is None:
                    continue
                owner, future, path = parked
                if owner is not stream or frame.path != path:
                    raise ValueError("reply does not belong to this connection and route")
                if not future.done():
                    future.set_result(frame)
        except (OSError, ValueError) as exc:
            reason = str(exc)
        finally:
            if self._stream is stream:
                self._stream = None
            for owner, future, _ in list(self._pending.values()):
                if owner is stream and not future.done():
                    future.set_exception(RemoteCallFailed(reason, outcome="unknown"))
            self._abandoned = {
                frame_id: entry for frame_id, entry in self._abandoned.items() if entry[0] is not stream
            }
            await stream.close()

    async def close(self) -> None:
        """Close the stream and stop the reader; no further call is admitted."""
        self._closed = True
        if self._stream is not None:
            await self._stream.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
