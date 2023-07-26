"""zmq Socket class"""

# Copyright (C) PyZMQ Developers
# Distributed under the terms of the Modified BSD License.

import errno as errno_mod

from ._cffi import ffi
from ._cffi import lib as C

nsp = new_sizet_pointer = lambda length: ffi.new('size_t*', length)

new_uint64_pointer = lambda: (ffi.new('uint64_t*'), nsp(ffi.sizeof('uint64_t')))
new_int64_pointer = lambda: (ffi.new('int64_t*'), nsp(ffi.sizeof('int64_t')))
new_int_pointer = lambda: (ffi.new('int*'), nsp(ffi.sizeof('int')))
new_binary_data = lambda length: (
    ffi.new('char[%d]' % (length)),
    nsp(ffi.sizeof('char') * length),
)

value_uint64_pointer = lambda val: (ffi.new('uint64_t*', val), ffi.sizeof('uint64_t'))
value_int64_pointer = lambda val: (ffi.new('int64_t*', val), ffi.sizeof('int64_t'))
value_int_pointer = lambda val: (ffi.new('int*', val), ffi.sizeof('int'))
value_binary_data = lambda val, length: (
    ffi.new('char[%d]' % (length + 1), val),
    ffi.sizeof('char') * length,
)

ZMQ_FD_64BIT = ffi.sizeof('ZMQ_FD_T') == 8

IPC_PATH_MAX_LEN = C.get_ipc_path_max_len()

import zmq
from zmq.constants import SocketOption, _OptType
from zmq.error import ZMQError, _check_rc, _check_version

from .message import Frame
from .utils import _retry_sys_call


def new_pointer_from_opt(option, length=0):
    opt_type = getattr(option, "_opt_type", _OptType.int)

    if opt_type == _OptType.int64 or (ZMQ_FD_64BIT and opt_type == _OptType.fd):
        return new_int64_pointer()
    elif opt_type == _OptType.bytes:
        return new_binary_data(length)
    else:
        # default
        return new_int_pointer()


def value_from_opt_pointer(option, opt_pointer, length=0):
    try:
        option = SocketOption(option)
    except ValueError:
        # unrecognized option,
        # assume from the future,
        # let EINVAL raise
        opt_type = _OptType.int
    else:
        opt_type = option._opt_type

    if opt_type == _OptType.bytes:
        return ffi.buffer(opt_pointer, length)[:]
    else:
        return int(opt_pointer[0])


def initialize_opt_pointer(option, value, length=0):
    opt_type = getattr(option, "_opt_type", _OptType.int)
    if opt_type == _OptType.int64 or (ZMQ_FD_64BIT and opt_type == _OptType.fd):
        return value_int64_pointer(value)
    elif opt_type == _OptType.bytes:
        return value_binary_data(value, length)
    else:
        return value_int_pointer(value)


class Socket:
    context = None
    socket_type = None
    _zmq_socket = None
    _closed = None
    _ref = None
    _shadow = False
    copy_threshold = 0

    def __init__(self, context=None, socket_type=None, shadow=0, copy_threshold=None):
        if copy_threshold is None:
            copy_threshold = zmq.COPY_THRESHOLD
        self.copy_threshold = copy_threshold

        self.context = context
        if shadow:
            self._zmq_socket = ffi.cast("void *", shadow)
            self._shadow = True
        else:
            self._shadow = False
            self._zmq_socket = C.zmq_socket(context._zmq_ctx, socket_type)
        if self._zmq_socket == ffi.NULL:
            raise ZMQError()
        self._closed = False

    @property
    def underlying(self):
        """The address of the underlying libzmq socket"""
        return int(ffi.cast('size_t', self._zmq_socket))

    def _check_closed_deep(self):
        """thorough check of whether the socket has been closed,
        even if by another entity (e.g. ctx.destroy).

        Only used by the `closed` property.

        returns True if closed, False otherwise
        """
        if self._closed:
            return True
        try:
            self.get(zmq.TYPE)
        except ZMQError as e:
            if e.errno == zmq.ENOTSOCK:
                self._closed = True
                return True
            elif e.errno == zmq.ETERM:
                pass
            else:
                raise
        return False

    @property
    def closed(self):
        return self._check_closed_deep()

    def close(self, linger=None):
        rc = 0
        if not self._closed and hasattr(self, '_zmq_socket'):
            if self._zmq_socket is not None:
                if linger is not None:
                    self.set(zmq.LINGER, linger)
                rc = C.zmq_close(self._zmq_socket)
            self._closed = True
        if rc < 0:
            _check_rc(rc)

    def bind(self, address):
        if isinstance(address, str):
            address_b = address.encode('utf8')
        else:
            address_b = address
        if isinstance(address, bytes):
            address = address_b.decode('utf8')
        rc = C.zmq_bind(self._zmq_socket, address_b)
        if rc < 0:
            if IPC_PATH_MAX_LEN and C.zmq_errno() == errno_mod.ENAMETOOLONG:
                path = address.split('://', 1)[-1]
                msg = (
                    'ipc path "{}" is longer than {} '
                    'characters (sizeof(sockaddr_un.sun_path)).'.format(
                        path, IPC_PATH_MAX_LEN
                    )
                )
                raise ZMQError(C.zmq_errno(), msg=msg)
            elif C.zmq_errno() == errno_mod.ENOENT:
                path = address.split('://', 1)[-1]
                msg = f'No such file or directory for ipc path "{path}".'
                raise ZMQError(C.zmq_errno(), msg=msg)
            else:
                _check_rc(rc)

    def unbind(self, address):
        _check_version((3, 2), "unbind")
        if isinstance(address, str):
            address = address.encode('utf8')
        rc = C.zmq_unbind(self._zmq_socket, address)
        _check_rc(rc)

    def connect(self, address):
        if isinstance(address, str):
            address = address.encode('utf8')
        rc = C.zmq_connect(self._zmq_socket, address)
        _check_rc(rc)

    def disconnect(self, address):
        _check_version((3, 2), "disconnect")
        if isinstance(address, str):
            address = address.encode('utf8')
        rc = C.zmq_disconnect(self._zmq_socket, address)
        _check_rc(rc)

    def set(self, option, value):
        length = None
        if isinstance(value, str):
            raise TypeError("unicode not allowed, use bytes")

        try:
            option = SocketOption(option)
        except ValueError:
            # unrecognized option,
            # assume from the future,
            # let EINVAL raise
            opt_type = _OptType.int
        else:
            opt_type = option._opt_type

        if isinstance(value, bytes):
            if opt_type != _OptType.bytes:
                raise TypeError("not a bytes sockopt: %s" % option)
            length = len(value)

        c_value_pointer, c_sizet = initialize_opt_pointer(option, value, length)

        _retry_sys_call(
            C.zmq_setsockopt,
            self._zmq_socket,
            option,
            ffi.cast('void*', c_value_pointer),
            c_sizet,
        )

    def get(self, option):
        try:
            option = SocketOption(option)
        except ValueError:
            # unrecognized option,
            # assume from the future,
            # let EINVAL raise
            opt_type = _OptType.int
        else:
            opt_type = option._opt_type

        c_value_pointer, c_sizet_pointer = new_pointer_from_opt(option, length=255)

        _retry_sys_call(
            C.zmq_getsockopt, self._zmq_socket, option, c_value_pointer, c_sizet_pointer
        )

        sz = c_sizet_pointer[0]
        v = value_from_opt_pointer(option, c_value_pointer, sz)
        if (
            option != zmq.SocketOption.ROUTING_ID
            and opt_type == _OptType.bytes
            and v.endswith(b'\0')
        ):
            v = v[:-1]
        return v

    def _send_copy(self, buf, flags):
        """Send a copy of a bufferable"""
        zmq_msg = ffi.new('zmq_msg_t*')
        if not isinstance(buf, bytes):
            # cast any bufferable data to bytes via memoryview
            buf = memoryview(buf).tobytes()

        c_message = ffi.new('char[]', buf)
        rc = C.zmq_msg_init_size(zmq_msg, len(buf))
        _check_rc(rc)
        C.memcpy(C.zmq_msg_data(zmq_msg), c_message, len(buf))
        _retry_sys_call(C.zmq_msg_send, zmq_msg, self._zmq_socket, flags)
        rc2 = C.zmq_msg_close(zmq_msg)
        _check_rc(rc2)

    def _send_frame(self, frame, flags):
        """Send a Frame on this socket in a non-copy manner."""
        # Always copy the Frame so the original message isn't garbage collected.
        # This doesn't do a real copy, just a reference.
        frame_copy = frame.fast_copy()
        zmq_msg = frame_copy.zmq_msg
        _retry_sys_call(C.zmq_msg_send, zmq_msg, self._zmq_socket, flags)
        tracker = frame_copy.tracker
        frame_copy.close()
        return tracker

    def send(self, data, flags=0, copy=False, track=False):
        if isinstance(data, str):
            raise TypeError("Message must be in bytes, not a unicode object")

        if copy and not isinstance(data, Frame):
            return self._send_copy(data, flags)
        else:
            close_frame = False
            if isinstance(data, Frame):
                if track and not data.tracker:
                    raise ValueError('Not a tracked message')
                frame = data
            else:
                if self.copy_threshold:
                    buf = memoryview(data)
                    # always copy messages smaller than copy_threshold
                    if buf.nbytes < self.copy_threshold:
                        self._send_copy(buf, flags)
                        return zmq._FINISHED_TRACKER
                frame = Frame(data, track=track, copy_threshold=self.copy_threshold)
                close_frame = True

            tracker = self._send_frame(frame, flags)
            if close_frame:
                frame.close()
            return tracker

    def send_multipart(self, msg_parts, flags=0, copy=True, track=False):
        """Send a sequence of buffers as a multipart message.

        The zmq.SNDMORE flag is added to all msg parts before the last.

        Parameters
        ----------
        msg_parts : iterable
            A sequence of objects to send as a multipart message. Each element
            can be any sendable object (Frame, bytes, buffer-providers)
        flags : int, optional
            Any valid flags for :func:`Socket.send`.
            SNDMORE is added automatically for frames before the last.
        copy : bool, optional
            Should the frame(s) be sent in a copying or non-copying manner.
            If copy=False, frames smaller than self.copy_threshold bytes
            will be copied anyway.
        track : bool, optional
            Should the frame(s) be tracked for notification that ZMQ has
            finished with it (ignored if copy=True).

        Returns
        -------
        None : if copy or not track
        MessageTracker : if track and not copy
            a MessageTracker object, whose `pending` property will
            be True until the last send is completed.
        """
        # typecheck parts before sending:
        MsgPartTypes = (Frame, bytes, memoryview)
        for i, msg in enumerate(msg_parts):
            if isinstance(msg, MsgPartTypes):
                continue
            try:
                memoryview(msg)
            except Exception:
                rmsg = repr(msg)
                if len(rmsg) > 32:
                    rmsg = rmsg[:32] + '...'
                raise TypeError(
                    "Frame %i (%s) does not support the buffer interface."
                    % (
                        i,
                        rmsg,
                    )
                )

        send = self.send
        more_flags = zmq.SNDMORE | flags
        for msg in msg_parts[:-1]:
            send(msg, more_flags, copy=copy, track=track)
        # Send the last part without the extra SNDMORE flag.
        return send(msg_parts[-1], flags, copy=copy, track=track)

    def recv(self, flags=0, copy=True, track=False):
        if copy:
            zmq_msg = ffi.new('zmq_msg_t*')
            C.zmq_msg_init(zmq_msg)
        else:
            frame = zmq.Frame(track=track)
            zmq_msg = frame.zmq_msg

        try:
            _retry_sys_call(C.zmq_msg_recv, zmq_msg, self._zmq_socket, flags)
        except Exception:
            if copy:
                C.zmq_msg_close(zmq_msg)
            raise

        if not copy:
            return frame

        _buffer = ffi.buffer(C.zmq_msg_data(zmq_msg), C.zmq_msg_size(zmq_msg))
        _bytes = _buffer[:]
        rc = C.zmq_msg_close(zmq_msg)
        _check_rc(rc)
        return _bytes

    def recv_multipart(self, flags=0, copy=True, track=False):
        """Receive a multipart message as a list of bytes or Frame objects

        Parameters
        ----------
        flags : int, optional
            Any valid flags for :func:`Socket.recv`.
        copy : bool, optional
            Should the message frame(s) be received in a copying or non-copying manner?
            If False a Frame object is returned for each part, if True a copy of
            the bytes is made for each frame.
        track : bool, optional
            Should the message frame(s) be tracked for notification that ZMQ has
            finished with it? (ignored if copy=True)

        Returns
        -------
        msg_parts : list
            A list of frames in the multipart message; either Frames or bytes,
            depending on `copy`.

        Raises
        ------
        ZMQError
            for any of the reasons :func:`~Socket.recv` might fail
        """
        # Inline and specialize getsockopt to reuse pointers
        RCVMORE = zmq.RCVMORE
        c_value_pointer, c_sizet_pointer = new_int_pointer()
        sz = c_sizet_pointer[0]

        def hasmore():
            _retry_sys_call(
                C.zmq_getsockopt, self._zmq_socket, RCVMORE, c_value_pointer, c_sizet_pointer
            )
            return int(c_value_pointer[0])

        # have first part already, only loop while more to receive
        recv = self.recv
        parts = [recv(flags, copy=copy, track=track)]
        while hasmore():
            part = recv(flags, copy=copy, track=track)
            parts.append(part)
        return parts

    def proxy_to(self, other, max_loops=0):
        """s.proxy_to(other)

        Receive all available messages from s and send them
        to "other" in a tight loop, return when no more messages
        are available to be sent or an error arises. Will not
        raise zmq.Again when there's no more messages to
        retrieve, but it might if they cannot be sent.

        Parameters
        ----------
        other : socket to relay messages to

        max_loops : maximum amount of messages to relay, defaults
            to 0 which means infinity.

        Raises
        ------
        ZMQError
            always with the first error, except it will not
            raise zmq.Again when there's no more messages to
            retrieve, but it might if they cannot be sent.
        """
        flags = zmq.NOBLOCK
        copy = False
        while 1:
            try:
                msg = self.recv_multipart(flags, copy)
            except zmq.Again:
                break
            self.send_multipart(msg, flags, copy)
            if max_loops > 0:
                max_loops -= 1
                if max_loops <= 0:
                    break

    def monitor(self, addr, events=-1):
        """s.monitor(addr, flags)

        Start publishing socket events on inproc.
        See libzmq docs for zmq_monitor for details.

        Note: requires libzmq >= 3.2

        Parameters
        ----------
        addr : str
            The inproc url used for monitoring. Passing None as
            the addr will cause an existing socket monitor to be
            deregistered.
        events : int [default: zmq.EVENT_ALL]
            The zmq event bitmask for which events will be sent to the monitor.
        """

        _check_version((3, 2), "monitor")
        if events < 0:
            events = zmq.EVENT_ALL
        if addr is None:
            addr = ffi.NULL
        if isinstance(addr, str):
            addr = addr.encode('utf8')
        C.zmq_socket_monitor(self._zmq_socket, addr, events)


__all__ = ['Socket', 'IPC_PATH_MAX_LEN']
