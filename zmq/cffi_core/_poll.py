# coding: utf-8

from ._cffi import C, ffi, zmq_version_info

from .constants import *


def _make_zmq_pollitem(socket, flags):
    zmq_socket = socket._zmq_socket
    zmq_pollitem = ffi.new('zmq_pollitem_t*')
    zmq_pollitem.socket = zmq_socket
    zmq_pollitem.fd = 0
    zmq_pollitem.events = flags
    zmq_pollitem.revents = 0
    return zmq_pollitem[0]

def _make_zmq_pollitem_fromfd(socket_fd, flags):
    zmq_pollitem = ffi.new('zmq_pollitem_t*')
    zmq_pollitem.socket = ffi.NULL
    zmq_pollitem.fd = socket_fd
    zmq_pollitem.events = flags
    zmq_pollitem.revents = 0
    return zmq_pollitem[0]

def _cffi_poll(zmq_pollitem_list, poller, timeout=-1):
    if zmq_version_info()[0] == 2:
        timeout = timeout * 1000
    items = ffi.new('zmq_pollitem_t[]', zmq_pollitem_list)
    list_length = ffi.cast('int', len(zmq_pollitem_list))
    c_timeout = ffi.cast('long', timeout)
    C.zmq_poll(items, list_length, c_timeout)
    result = []
    for index in range(len(items)):
        if not items[index].socket == ffi.NULL:
            if items[index].revents > 0:
                result.append((poller._sockets[items[index].socket],
                               items[index].revents))
        else:
            result.append((items[index].fd, items[index].revents))

    return result

def zmq_poll(sockets, timeout):
    cffi_pollitem_list = []
    low_level_to_socket_obj = {}
    for item in sockets:
        if isinstance(item[0], int):
            low_level_to_socket_obj[item[0]] = item
            cffi_pollitem_list.append(_make_zmq_pollitem_fromfd(item[0], item[1]))
        else:
            low_level_to_socket_obj[item[0]._zmq_socket] = item
            cffi_pollitem_list.append(_make_zmq_pollitem(item[0], item[1]))
    items = ffi.new('zmq_pollitem_t[]', cffi_pollitem_list)
    list_length = ffi.cast('int', len(cffi_pollitem_list))
    c_timeout = ffi.cast('long', timeout)
    C.zmq_poll(items, list_length, c_timeout)
    result = []
    for index in range(len(items)):
        if not items[index].socket == ffi.NULL:
            if items[index].revents > 0:
                result.append((low_level_to_socket_obj[items[index].socket][0],
                            items[index].revents))
        else:
            result.append((items[index].fd, items[index].revents))
    return result

class Poller(object):
    def __init__(self):
        self.sockets_flags = {}
        self._sockets = {}
        self.c_sockets = {}

    @property
    def sockets(self):
        return self.sockets_flags

    def register(self, socket, flags=POLLIN|POLLOUT):
        if flags:
            self.sockets_flags[socket] = flags
            if isinstance(socket, int):
                self.c_sockets[socket] = _make_zmq_pollitem_fromfd(socket, flags)
            else:
                self.c_sockets[socket] =  _make_zmq_pollitem(socket, flags)
                self._sockets[socket._zmq_socket] = socket
        elif socket in self.sockets_flags:
            # uregister sockets registered with no events
            self.unregister(socket)
        else:
            # ignore new sockets with no events
            pass

    def modify(self, socket, flags=POLLIN|POLLOUT):
        self.register(socket, flags)

    def unregister(self, socket):
        del self.sockets_flags[socket]
        del self.c_sockets[socket]

        if not isinstance(socket, int):
            del self._sockets[socket._zmq_socket]

    def poll(self, timeout=None):
        if timeout is None:
            timeout = -1

        timeout = int(timeout)
        if timeout < 0:
            timeout = -1

        items =  _cffi_poll(self.c_sockets.values(),
                            self,
                            timeout=timeout)

        return items

__all__ = ['zmq_poll', 'Poller']
