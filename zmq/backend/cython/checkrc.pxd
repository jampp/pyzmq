from libc.errno cimport EINTR, EAGAIN
from cpython cimport PyErr_CheckSignals
from libzmq cimport zmq_errno, ZMQ_ETERM

cdef inline int _check_rc(int rc) except -1:
    """internal utility for checking zmq return condition
    
    and raising the appropriate Exception class
    """
    cdef int errno = zmq_errno()
    PyErr_CheckSignals()
    if rc < 0:
        if errno == EAGAIN:
            from zmq.error import Again
            raise Again(errno)
        elif errno == ZMQ_ETERM:
            from zmq.error import ContextTerminated
            raise ContextTerminated(errno)
        else:
            from zmq.error import ZMQError
            raise ZMQError(errno)
        # return -1
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
