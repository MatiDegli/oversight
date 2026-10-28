"""Trusted in-memory entry programs; neither is written into the runtime tree."""

FLAGS = ("-rn", "--pid", "--fork", "--kill-child=SIGKILL", "--mount-proc")
MARKER = "oversight-isolation-v1:"

LAUNCHER = r"""
import ctypes, os, signal, stat, sys
parent, binary = int(sys.argv[1]), sys.argv[2]
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG failed")
if os.getppid() != parent:
    sys.exit(125)
info = os.stat(binary)
if not os.path.isabs(binary) or info.st_mode & (stat.S_ISUID | stat.S_ISGID):
    sys.exit(125)
try:
    capabilities = os.getxattr(binary, "security.capability")
except OSError as error:
    import errno
    if error.errno not in (errno.ENODATA, errno.ENOTSUP):
        raise
else:
    if capabilities:
        sys.exit(125)
if libc.prctl(38, 1, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
os.execv(binary, [binary, *sys.argv[3:]])
"""

HELPER = r"""
import errno, json, os, socket, sys
from pathlib import Path
fd, ack, nonce, parent, net, pidns, mountns, procdev, marker, port = sys.argv[1:11]
fd, ack, parent, port = int(fd), int(ack), int(parent), int(port)
identity = {name: os.readlink('/proc/self/ns/' + name) for name in ('net', 'pid', 'mnt')}
assert os.getpid() == 1
assert identity['net'] != net and identity['pid'] != pidns and identity['mnt'] != mountns
assert os.readlink('/proc/1/ns/pid') == identity['pid']
assert os.stat('/proc').st_dev != int(procdev)
mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
device = os.stat('/proc').st_dev
mount = [line for line in mounts if line[4] == '/proc'
         and line[2] == str(os.major(device)) + ':' + str(os.minor(device))]
assert len(mount) == 1 and mount[0][3] == '/'
assert mount[0][mount[0].index('-') + 1] == 'proc'
assert not any(item.startswith(('shared:', 'master:')) for item in mount[0][6:])
assert not any(os.path.lexists('/proc/' + str(parent) + '/' + name)
               for name in ('cmdline', 'cwd', 'fd', 'maps', 'environ'))
# Only the freshly verified namespace's loopback; preserve its other interface flags.
try:
    import fcntl, struct
    SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 1
    with socket.socket() as control:
        request = struct.pack('16sH22x', b'lo', 0)
        flags = struct.unpack('16sH22x', fcntl.ioctl(control, SIOCGIFFLAGS, request))[1]
        fcntl.ioctl(control, SIOCSIFFLAGS, struct.pack('16sH22x', b'lo', flags | IFF_UP))
except Exception as error:
    # Setup is best effort: retain isolation and capture the diagnostic on stderr.
    import logging
    logging.warning('Loopback setup failed: %s', error)
refused = None
connect_errno = None
if port:
    with socket.socket() as client:
        client.settimeout(1)
        connect_errno = client.connect_ex(('127.0.0.1', port))
        refused = connect_errno in (errno.ECONNREFUSED, errno.ENETUNREACH)
    assert refused
report = dict(nonce=nonce, pid=1, identities=identity, procdev=os.stat('/proc').st_dev,
              private_proc=True, server_hidden=True, refused=refused, connect_errno=connect_errno)
os.write(fd, json.dumps(report).encode() + b'\n')
os.close(fd)
assert os.read(ack, 1) == b'1'
os.close(ack)
os.execv(sys.argv[11], sys.argv[11:])
"""
