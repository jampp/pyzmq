from cpython cimport PyErr_CheckSignals
from libc.errno cimport EAGAIN, EINTR

from .libzmq cimport ZMQ_ETERM, zmq_errno


cdef inline int _check_rc(int rc, bint error_without_errno=True) except -1:
    """internal utility for checking zmq return condition

    and raising the appropriate Exception class
    """
    PyErr_CheckSignals()
    if rc == -1: # if rc < -1, it's a bug in libzmq. Should we warn?
        _raise_errno(error_without_errno)
    return 0


cdef inline int _check_rc_nosig(int rc, bint error_without_errno=True) except -1:
    """internal utility for checking zmq return condition

    and raising the appropriate Exception class. This version does
    not check for signals so it's a bit faster.
    """
    if rc == -1: # if rc < -1, it's a bug in libzmq. Should we warn?
        _raise_errno(error_without_errno)
    return 0


cdef inline bint _check_interrupted(int rc) noexcept nogil:
    """internal utility for checking zmq interrupt condition

    Returns whether check_rc would raise InterruptedSystemCall
    """
    return rc == -1 and zmq_errno() == EINTR


cdef inline int _raise_errno(bint error_without_errno=True) except -1:
    return _raise_errno_saved(zmq_errno(), error_without_errno)


cdef inline int _raise_errno_saved(int errno, bint error_without_errno=True) except -1:
    """internal utility for checking zmq return condition

    and raising the appropriate Exception class
    """
    if errno == 0 and not error_without_errno:
        return 0
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
