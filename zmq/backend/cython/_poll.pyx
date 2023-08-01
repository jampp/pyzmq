"""0MQ polling related functions and classes."""

#
#    Copyright (c) 2010-2011 Brian E. Granger & Min Ragan-Kelley
#
#    This file is part of pyzmq.
#
#    pyzmq is free software; you can redistribute it and/or modify it under
#    the terms of the Lesser GNU General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    pyzmq is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    Lesser GNU General Public License for more details.
#
#    You should have received a copy of the Lesser GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

from libc.stdlib cimport free, malloc, realloc

from .libzmq cimport ZMQ_VERSION_MAJOR
from .libzmq cimport zmq_poll as zmq_poll_c
from .libzmq cimport zmq_pollitem_t
from .libzmq cimport zmq_errno
from .socket cimport Socket

import sys

try:
    from time import monotonic
except ImportError:
    from time import clock as monotonic

import warnings

from .checkrc cimport _check_rc, _raise_errno_saved

from zmq.error import InterruptedSystemCall

#-----------------------------------------------------------------------------
# Polling related methods
#-----------------------------------------------------------------------------


cdef inline object _zmq_poll(sockets, zmq_pollitem_t *pollitems, int nsockets, long timeout):
    """Poll a set of 0MQ sockets, native file descs. or sockets.

    Parameters
    ----------
    nsockets : number of entries in pollitems

    pollitems : list of tuples zmq_pollitem_t structs 
        Each element of this list contains a socket or file descriptor
        and a flags. The flags can be zmq.POLLIN (for detecting
        for incoming messages), zmq.POLLOUT (for detecting that send is OK)
        or zmq.POLLIN|zmq.POLLOUT for detecting both.

    timeout : int
        The number of milliseconds to poll for. Negative means no timeout.
    """
    cdef int rc, errno
    cdef int ms_passed
    cdef double start, current

    while True:
        start = monotonic()
        with nogil:
            rc = zmq_poll_c(pollitems, nsockets, timeout)
            if rc == -1:
                errno = zmq_errno()
        try:
            if rc == -1:
                _raise_errno_saved(errno)
        except InterruptedSystemCall:
            if timeout > 0:
                current = monotonic()
                ms_passed = int(1000 * (current - start))
                if ms_passed < 0:
                    # don't allow negative ms_passed,
                    # which can happen on old Python versions without time.monotonic.
                    warnings.warn(
                        "Negative elapsed time for interrupted poll: %s."
                        "  Did the clock change?" % ms_passed,
                        RuntimeWarning)
                    # treat this case the same as no time passing,
                    # since it should be rare and not happen twice in a row.
                    ms_passed = 0
                timeout = max(0, timeout - ms_passed)
            continue
        else:
            break

    cdef list results = []
    cdef int i
    cdef object s
    cdef short revents

    if rc:
        for i in range(nsockets):
            revents = pollitems[i].revents
            # for compatibility with select.poll:
            # - only return sockets with non-zero status
            # - return the fd for plain sockets
            if revents > 0:
                if pollitems[i].socket != NULL:
                    s = sockets[i][0]
                else:
                    s = pollitems[i].fd
                results.append((s, revents))
    return results


cdef inline int _fill_pollitems(sockets, zmq_pollitem_t *pollitems, int nsockets) except -1:
    if nsockets == 0:
        return 0

    cdef object s
    cdef int fileno, i
    cdef short events

    for i in range(nsockets):
        s, events = sockets[i]
        if isinstance(s, Socket):
            pollitems[i].socket = (<Socket>s).handle
            pollitems[i].fd = 0
            pollitems[i].events = events
            pollitems[i].revents = 0
        elif isinstance(s, int):
            pollitems[i].socket = NULL
            pollitems[i].fd = s
            pollitems[i].events = events
            pollitems[i].revents = 0
        elif hasattr(s, 'fileno'):
            try:
                fileno = int(s.fileno())
            except:
                raise ValueError('fileno() must return a valid integer fd')
            else:
                pollitems[i].socket = NULL
                pollitems[i].fd = fileno
                pollitems[i].events = events
                pollitems[i].revents = 0
        else:
            raise TypeError(
                "Socket must be a 0MQ socket, an integer fd or have "
                "a fileno() method: %r" % s
            )

    return 0
    

def zmq_poll(sockets, long timeout=-1):
    """zmq_poll(sockets, timeout=-1)

    Poll a set of 0MQ sockets, native file descs. or sockets.

    Parameters
    ----------
    sockets : list of tuples of (socket, flags)
        Each element of this list is a two-tuple containing a socket
        and a flags. The socket may be a 0MQ socket or any object with
        a ``fileno()`` method. The flags can be zmq.POLLIN (for detecting
        for incoming messages), zmq.POLLOUT (for detecting that send is OK)
        or zmq.POLLIN|zmq.POLLOUT for detecting both.
    timeout : int
        The number of milliseconds to poll for. Negative means no timeout.
    """
    cdef int free_pollitems
    cdef zmq_pollitem_t *pollitems = NULL
    cdef zmq_pollitem_t _sml_pollitems[16]
    cdef int nsockets = <int>len(sockets)

    if nsockets == 0:
        return []

    if nsockets <= 16:
        free_pollitems = 0
        pollitems = _sml_pollitems
    else:
        free_pollitems = 1
        pollitems = <zmq_pollitem_t *>malloc(nsockets*sizeof(zmq_pollitem_t))
        if pollitems == NULL:
            raise MemoryError("Could not allocate poll items")

    if ZMQ_VERSION_MAJOR < 3:
        # timeout is us in 2.x, ms in 3.x
        # expected input is ms (matches 3.x)
        timeout = 1000*timeout

    try:
        _fill_pollitems(sockets, pollitems, nsockets)
        results = _zmq_poll(sockets, pollitems, nsockets, timeout)
    finally:
        if free_pollitems:
            free(pollitems)

    return results


cdef class PollerBase:
    """A stateful poll interface that mirrors Python's built-in poll."""

    cdef zmq_pollitem_t *pollitems
    cdef size_t nitems
    cdef int dirty

    def __init__(self):
        self.pollitems = NULL
        self.nitems = 0
        self.dirty = 1

    def __dealloc__(self):
        if self.pollitems != NULL:
            free(self.pollitems)
            self.pollitems = NULL

    def on_modified(self):
        self.dirty = 1

    def _poll(self, sockets, long timeout=-1):
        cdef size_t nsockets = len(sockets)
        if self.dirty or nsockets != self.nitems or self.pollitems == NULL:
            self.pollitems = <zmq_pollitem_t*>realloc(<void*>self.pollitems, nsockets*sizeof(zmq_pollitem_t))
            if self.pollitems == NULL:
                raise MemoryError("Could not allocate poll items")
            self.nitems = nsockets
            _fill_pollitems(sockets, self.pollitems, self.nitems)
            self.dirty = 0
        return _zmq_poll(sockets, self.pollitems, self.nitems, timeout)


#-----------------------------------------------------------------------------
# Symbols to export
#-----------------------------------------------------------------------------

__all__ = [
    'zmq_poll',
    'PollerBase',
]
