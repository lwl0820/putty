"""核验两种私有 ACL 契约；内存描述符回归不能替代 Windows 原生读回。"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from private_data import PrivateData, ValidationError


def _pointer(output, value):
    C.cast(output, C.POINTER(C.c_void_p))[0] = value


def _sid(pointer):
    return C.cast(pointer, C.POINTER(C.c_uint32))[0]


def descriptor(entries, *, owner=1, protected=True):
    private = PrivateData.__new__(PrivateData)
    user, system, actual_owner = C.c_uint32(1), C.c_uint32(2), C.c_uint32(owner)
    aces = [C.create_string_buffer(struct.pack("<BBHII", kind, flags, 12, mask, sid))
            for sid, flags, mask, kind in entries]
    acl = C.create_string_buffer(struct.pack("<BBHHH", 2, 0, 8 + len(aces) * 12, len(aces), 0))
    private.user, private.system = C.addressof(user), C.addressof(system)
    private._test_buffers = (user, system, actual_owner, aces, acl)

    def get_security(handle, object_type, information, output_owner, group, output_acl, sacl, output_descriptor):
        _pointer(output_owner, C.addressof(actual_owner))
        _pointer(output_acl, C.addressof(acl))
        _pointer(output_descriptor, 123)
        return 0

    def control(value, output_control, revision):
        C.cast(output_control, C.POINTER(W.WORD))[0] = 0x1000 if protected else 0
        return True

    def get_ace(value, index, output):
        _pointer(output, C.addressof(aces[index]))
        return True

    private.security = SimpleNamespace(
        GetSecurityInfo=get_security, GetSecurityDescriptorControl=control, GetAce=get_ace,
        EqualSid=lambda left, right: _sid(left) == _sid(right))
    private.kernel = SimpleNamespace(LocalFree=Mock())
    return private


class PrivateDataContractTest(unittest.TestCase):
    def test_owner_only_export_is_accepted_without_rewriting_permissions(self):
        private = descriptor([(1, 0, 0x1F01FF, 0)])
        private._open = Mock(return_value=5)
        private.kernel.CloseHandle = Mock(return_value=True)
        private.check_file(Path("unused.ppk"), owner_only=True)
        private._open.assert_called_once_with(Path("unused.ppk"), False, 0x20080)
        private.kernel.CloseHandle.assert_called_once_with(5)
        private.kernel.LocalFree.assert_called_once()
        self.assertEqual(private.kernel.LocalFree.call_args.args[0].value, 123)

    def test_default_data_still_requires_both_user_and_system(self):
        descriptor([(1, 0, 0x1F01FF, 0), (2, 0, 0x1F01FF, 0)])._check(5, False)
        descriptor([(1, 3, 0x1F01FF, 0), (2, 3, 0x1F01FF, 0)])._check(5, True)
        with self.assertRaises(ValidationError):
            descriptor([(1, 0, 0x1F01FF, 0)])._check(5, False)

    def test_export_rejects_extra_identities_inheritance_and_changed_access(self):
        for entries in ([], [(1, 0, 0x1F01FF, 0), (2, 0, 0x1F01FF, 0)],
                        [(2, 0, 0x1F01FF, 0)], [(3, 0, 0x1F01FF, 0)],
                        [(1, 16, 0x1F01FF, 0)], [(1, 0, 0x120089, 0)], [(1, 0, 0x1F01FF, 1)]):
            with self.subTest(entries=entries), self.assertRaises(ValidationError):
                descriptor(entries)._check(5, False, owner_only=True)
        for fields in ({"owner": 2}, {"protected": False}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                descriptor([(1, 0, 0x1F01FF, 0)], **fields)._check(5, False, owner_only=True)

    def test_default_data_rejects_duplicate_and_broad_permissions(self):
        for entries in ([(1, 0, 0x1F01FF, 0), (1, 0, 0x1F01FF, 0)],
                        [(1, 0, 0x1F01FF, 0), (3, 0, 0x1F01FF, 0)]):
            with self.subTest(entries=entries), self.assertRaises(ValidationError):
                descriptor(entries)._check(5, False)

    def test_export_mode_cannot_harden_files_or_be_used_for_directories(self):
        private = descriptor([(1, 0, 0x1F01FF, 0)])
        private._open = Mock()
        with self.assertRaises(ValidationError):
            private.check_file(Path("unused.ppk"), harden=True, owner_only=True)
        private._open.assert_not_called()
        with self.assertRaises(ValidationError):
            private._check(5, True, owner_only=True)

    def test_export_mode_keeps_handle_type_link_and_final_path_checks(self):
        path = Path.cwd() / ".tmp" / "acl-memory.ppk"
        for attributes, links, same_path in ((0x400, 1, True), (0x10, 1, True),
                                             (0, 2, True), (0, 1, False)):
            with self.subTest(attributes=attributes, links=links, same_path=same_path):
                private = descriptor([(1, 0, 0x1F01FF, 0)])
                private.security.GetSecurityInfo = Mock()

                def information(handle, output):
                    output[0], output[10] = attributes, links
                    return True

                def final_path(handle, output, capacity, flags):
                    output.value = "\\\\?\\" + str(path if same_path else path.with_name("other.ppk"))
                    return len(output.value)

                private.kernel.CreateFileW = Mock(return_value=5)
                private.kernel.GetFileInformationByHandle = information
                private.kernel.GetFinalPathNameByHandleW = final_path
                private.kernel.CloseHandle = Mock(return_value=True)
                with patch("private_data.project_path", return_value=path) as validate:
                    with self.assertRaises(ValidationError):
                        private.check_file(path, owner_only=True)
                validate.assert_called_once_with(path, must_exist=True)
                private.kernel.CreateFileW.assert_called_once_with(str(path), 0x20080, 3, None, 3, 0x200000, None)
                private.kernel.CloseHandle.assert_called_once_with(5)
                private.security.GetSecurityInfo.assert_not_called()

    def test_export_acl_failure_frees_descriptor_and_closes_read_handle(self):
        private = descriptor([(1, 0, 0x1F01FF, 0)], protected=False)
        private._open = Mock(return_value=5)
        private.kernel.CloseHandle = Mock(return_value=True)
        with self.assertRaises(ValidationError):
            private.check_file(Path("unused.ppk"), owner_only=True)
        private.kernel.LocalFree.assert_called_once()
        private.kernel.CloseHandle.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
