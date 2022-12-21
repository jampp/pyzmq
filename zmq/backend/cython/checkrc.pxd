from cpython cimport PyErr_CheckSignals
from libc.errno cimport EAGAIN, EINTR

from .libzmq cimport ZMQ_ETERM, zmq_errno


cdef inline int _check_rc(int rc, bint error_without_errno=True) except -1:
    """internal utility for checking zmq return condition

    and raising the appropriate Exception class
    """
    cdef int errno = zmq_errno()
    PyErr_CheckSignals()
    if errno == 0 and not error_without_errno:
        return 0
    if rc == -1: # if rc < -1, it's a bug in libzmq. Should we warn?
        if errno == EINTR:
            from zmq.error import InterruptedSystemCall
            raise InterruptedSystemCall(errno)
        elif errno == EAGAIN:
            from zmq.error import Again
            raise Again(errno)
        elif errno == ZMQ_ETERM:
            from zmq.error import ContextTerminated
            raise ContextTerminated(errno)
        else:
            from zmq.error import ZMQError
            raise ZMQError(errno)
    return 0

cdef inline int _raise_errno(int errno) except -1:
    """internal utility for raising the appropriate Exception class
    """
    PyErr_CheckSignals()
    if errno == EAGAIN:
        from zmq.error import Again
        raise Again(errno)
        # return -1
    elif errno == ZMQ_ETERM:
        from zmq.error import ContextTerminated
        raise ContextTerminated(errno)
        # return -1
    else:
        from zmq.error import ZMQError
        raise ZMQError(errno)
        # return -1
    return 0
