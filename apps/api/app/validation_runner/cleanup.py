"""Bounded handle-based Windows deletion of registered, VM-detached artifacts.

No recursive path deletion, provisioning, launch, automatic retry or capacity
release. The protected supervisor owns durable effect intent and reconciliation.
"""

import ctypes
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from struct import unpack_from
from time import monotonic

from app.core.errors import DomainError
from app.models.validation_cleanup import StorageRegistration
from app.tool_execution.filesystem import open_directory, windows_final_path


def refuse():
    raise DomainError(
        "VALIDATION_CLEANUP_UNCERTAIN", "Owned storage cleanup needs protected reconciliation.", 409
    ) from None


def private_acl(fd, service_sid):
    """Inspect the opened object's protected owner/DACL; never change permissions."""
    import msvcrt
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True, winmode=0x800)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    get = advapi.GetSecurityInfo
    get.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD] + [
        ctypes.POINTER(ctypes.c_void_p)
    ] * 5
    get.restype = wintypes.DWORD
    control = advapi.GetSecurityDescriptorControl
    control.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    control.restype = wintypes.BOOL
    ace = advapi.GetAce
    ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    ace.restype = wintypes.BOOL
    convert = advapi.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()

    def sid_string(pointer):
        output = ctypes.c_void_p()
        if not convert(pointer, ctypes.byref(output)):
            refuse()
        try:
            return ctypes.wstring_at(output.value)
        finally:
            kernel.LocalFree(output)

    try:
        # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
        if get(
            msvcrt.get_osfhandle(fd),
            1,
            5,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        ):
            refuse()
        flags, revision = wintypes.WORD(), wintypes.DWORD()
        if (
            not owner.value
            or not dacl.value
            or not control(descriptor, ctypes.byref(flags), ctypes.byref(revision))
            or not flags.value & 0x1000
            or sid_string(owner) != service_sid
        ):
            refuse()
        # ACL header: revision/size/ACE count; GetAce handles actual entry bounds.
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        if count > 64:
            refuse()
        permitted = {service_sid, "S-1-5-18", "S-1-5-32-544"}
        for index in range(count):
            entry = ctypes.c_void_p()
            if not ace(dacl, index, ctypes.byref(entry)):
                refuse()
            kind = ctypes.c_ubyte.from_address(entry.value).value
            if kind == 1:  # ACCESS_DENIED_ACE cannot grant access.
                continue
            if kind != 0 or sid_string(entry.value + 8) not in permitted:
                refuse()
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)


def ordinary_streams(fd, kernel, *, directory):
    """Enumerate opened-handle streams; named ADS and incomplete replies deny."""
    import msvcrt
    from ctypes import wintypes

    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(65536)
    if not query(msvcrt.get_osfhandle(fd), 7, buffer, len(buffer)):
        if directory and ctypes.get_last_error() == 38:  # ERROR_HANDLE_EOF: no streams.
            return
        refuse()
    position = 0
    raw = buffer.raw
    for _ in range(32):
        next_offset, name_size = unpack_from("<II", raw, position)
        end = position + 24 + name_size
        if name_size % 2 or end > len(raw) or name_size > 512:
            refuse()
        if raw[position + 24 : end].decode("utf-16-le") != "::$DATA":
            refuse()
        if not next_offset:
            return
        if next_offset < 24 + name_size or position + next_offset > len(raw) - 24:
            refuse()
        position += next_offset
    refuse()


class DeletionHandle:
    def __init__(self, fd, kernel):
        self.fd, self.kernel = fd, kernel

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def delete(self):
        import msvcrt
        from ctypes import wintypes

        method = self.kernel.SetFileInformationByHandle
        method.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        method.restype = wintypes.BOOL
        # FILE_DISPOSITION_INFO contains BOOLEAN (one byte), not Win32 BOOL.
        disposition = ctypes.c_ubyte(1)
        if not method(msvcrt.get_osfhandle(self.fd), 4, ctypes.byref(disposition), 1):
            refuse()
        self.close()


@contextmanager
def deletion_handle(path, identity, *, directory, service_sid, deletable=True, allow_missing=False):
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    # READ_CONTROL + GENERIC_READ + DELETE; deny write/delete sharing. Directory
    # backup semantics and OPEN_REPARSE_POINT inspect the link itself, not target.
    access = 0x80020000 | (0x10000 if deletable else 0)
    handle = create(str(path), access, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        if allow_missing and ctypes.get_last_error() in {2, 3}:
            # Only native FILE/PATH_NOT_FOUND reconciles an already absent target.
            # Access denial, sharing violations and service errors stay uncertain.
            yield None
            return
        refuse()
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    opened = DeletionHandle(fd, kernel)
    try:
        info = os.fstat(fd)
        if (
            (info.st_dev, info.st_ino) != (identity.volume_id, identity.file_id)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or stat.S_ISDIR(info.st_mode) != directory
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or os.path.normcase(windows_final_path(fd)) != os.path.normcase(str(path))
        ):
            refuse()
        private_acl(fd, service_sid)
        ordinary_streams(fd, kernel, directory=directory)
        yield opened
    finally:
        opened.close()


def cleanup_registered_storage(
    root, registration, *, authority_check, require_vm_absent, wall_seconds=120
):
    """Delete only registered native IDs after independent VM absence checks.

    authority_check(root, registration) must authenticate the original protected
    creation record, host identity and durable cleanup intent. It must authorize
    cleanup after execution lease expiry/stop without granting execution. VM
    absence is freshly measured by the native management adapter, never JSON from
    a guest. Callbacks have no defaults. Capacity remains quarantined until the
    supervisor persists successful reconciliation and confirms all other resources.
    """
    if os.name != "nt":
        refuse()
    if type(wall_seconds) is not int or not 1 <= wall_seconds <= 300:
        refuse()
    deadline = monotonic() + wall_seconds
    registration = StorageRegistration.model_validate(registration.model_dump(mode="json"))
    root = Path(root)
    if not root.is_absolute() or str(root).startswith("\\\\") or root == Path(root.anchor):
        refuse()
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    drive = kernel.GetDriveTypeW
    drive.argtypes = [wintypes.LPCWSTR]
    drive.restype = wintypes.UINT
    if drive(root.anchor) != 3:  # DRIVE_FIXED only; never mapped network storage.
        refuse()

    def guard():
        if monotonic() > deadline:
            refuse()
        authority_check(root, registration)
        require_vm_absent(registration.binding)
        if monotonic() > deadline:
            refuse()

    guard()
    try:
        with ExitStack() as stack:
            for path in (*reversed(root.parents), root):
                open_directory(stack, path, internal=True)
            info = os.stat(root, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (
                registration.dedicated_root_identity.volume_id,
                registration.dedicated_root_identity.file_id,
            ):
                refuse()
            # Root ACL is checked through a separately opened read handle below.
            # The allocation is the only deletable directory; ancestors stay pinned.
            allocation = root / registration.allocation_name
            # Root handle has no DELETE access and is never a deletion target.
            stack.enter_context(
                deletion_handle(
                    root,
                    registration.dedicated_root_identity,
                    directory=True,
                    service_sid=registration.service_sid,
                    deletable=False,
                )
            )
            handles = {
                "": stack.enter_context(
                    deletion_handle(
                        allocation,
                        registration.allocation_identity,
                        directory=True,
                        service_sid=registration.service_sid,
                        allow_missing=True,
                    )
                )
            }
            children = {"": set()}
            for item in registration.entries:
                guard()
                parent, _, leaf = item.path.rpartition("/")
                if item.kind == "directory":
                    children[item.path] = set()
                handles[item.path] = stack.enter_context(
                    deletion_handle(
                        allocation.joinpath(*item.path.split("/")),
                        item.identity,
                        directory=item.kind == "directory",
                        service_sid=registration.service_sid,
                        allow_missing=True,
                    )
                )
                if handles[item.path] is not None:
                    children[parent].add(leaf)
            # Full preflight before any deletion; no filesystem enumeration chooses
            # deletion targets. Unexpected checkpoints/files block all effects.
            for parent, expected in children.items():
                if handles[parent] is None:
                    if expected:
                        refuse()
                    continue
                if (
                    set(
                        os.listdir(
                            allocation.joinpath(*parent.split("/")) if parent else allocation
                        )
                    )
                    != expected
                ):
                    refuse()
            for name in sorted(
                handles, key=lambda name: (name.count("/"), len(name)), reverse=True
            ):
                if handles[name] is None:
                    continue
                guard()
                handles[name].delete()
            guard()
            if allocation.exists():
                refuse()
        return {
            "registered_storage_absent": True,
            "capacity_released": False,
            "execution_authorized": False,
        }
    except DomainError:
        raise
    except (OSError, ValueError, TypeError):
        refuse()
