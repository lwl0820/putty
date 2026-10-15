"""只读取 PE 元数据，检查 x64 与普通、延迟依赖；从不执行文件。"""
import re
import struct

from .common import ValidationError


def pe_summary(data):
    def unpack(format_string, offset):
        size = struct.calcsize(format_string)
        if offset < 0 or offset > len(data) - size:
            raise ValidationError("PE 字段越过文件边界")
        return struct.unpack_from(format_string, data, offset)

    if len(data) < 64 or data[:2] != b"MZ":
        raise ValidationError("文件没有有效 DOS 头")
    pe_offset, = unpack("<I", 0x3c)
    if pe_offset < 64 or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValidationError("文件没有有效 PE 签名")
    machine, count, _, _, _, optional_size, _ = unpack("<HHIIIHH", pe_offset + 4)
    optional = pe_offset + 24
    if machine != 0x8664 or unpack("<H", optional)[0] != 0x20b or not 1 <= count <= 96:
        raise ValidationError("便携文件必须为有界 x64 PE32+")
    if optional_size < 240 or unpack("<I", optional + 108)[0] != 16:
        raise ValidationError("PE 可选头或数据目录数量不符合固定格式")
    headers_size, = unpack("<I", optional + 60)
    if not optional + optional_size + count * 40 <= headers_size <= len(data):
        raise ValidationError("PE 节表或头部长度无效")
    sections = []
    for index in range(count):
        header = optional + optional_size + index * 40
        virtual_size, rva, raw_size, raw = unpack("<IIII", header + 8)
        span = max(virtual_size, raw_size)
        if (not span or rva + span > 0x100000000 or
                (raw_size and (raw < headers_size or raw + raw_size > len(data)))):
            raise ValidationError("PE 节范围越界")
        for old_rva, old_span, old_raw, old_size in sections:
            if rva < old_rva + old_span and old_rva < rva + span:
                raise ValidationError("PE 虚拟节范围重叠")
            if raw_size and old_size and raw < old_raw + old_size and old_raw < raw + raw_size:
                raise ValidationError("PE 文件节范围重叠")
        sections.append((rva, span, raw, raw_size))

    def region(rva, size):
        if size < 0 or rva + size > 0x100000000:
            raise ValidationError("PE RVA 长度溢出")
        for base, _, raw, raw_size in sections:
            delta = rva - base
            if 0 <= delta and delta + size <= raw_size:
                return raw + delta, raw + raw_size
        raise ValidationError("PE RVA 不属于完整文件数据")

    def text(rva):
        start, end = region(rva, 1)
        terminator = data.find(b"\0", start, min(end, start + 256))
        if terminator <= start:
            raise ValidationError("PE 字符串缺少有界结束符")
        try:
            value = data[start:terminator].decode("ascii")
        except UnicodeDecodeError:
            raise ValidationError("PE 名称不是 ASCII") from None
        if any(ord(char) < 32 for char in value):
            raise ValidationError("PE 名称含控制字符")
        return value

    def directory(index):
        rva, size = unpack("<II", optional + 112 + index * 8)
        if bool(rva) != bool(size):
            raise ValidationError("PE 数据目录的地址和长度不匹配")
        if rva:
            region(rva, size)
        return rva, size

    def imports(index, width):
        rva, size = directory(index)
        if not rva:
            return []
        if size < width or size > width * 128:
            raise ValidationError("PE 导入表长度超限")
        position, _ = region(rva, size)
        names, terminated = [], False
        for offset in range(0, size - width + 1, width):
            fields = unpack("<" + "I" * (width // 4), position + offset)
            if not any(fields):
                terminated = True
                break
            if width == 32 and fields[0] != 1:
                raise ValidationError("延迟导入必须使用 RVA 形式")
            name = text(fields[1 if width == 32 else 3]).lower()
            if not re.fullmatch(r"[a-z0-9_]+\.dll", name) or name in names:
                raise ValidationError("PE 导入名称重复或不安全")
            names.append(name)
        if not terminated:
            raise ValidationError("PE 导入表没有有界结束标记")
        return sorted(names)

    exports = []
    rva, size = directory(0)
    if rva:
        if size < 40:
            raise ValidationError("PE 导出表被截断")
        position, _ = region(rva, 40)
        names_count, = unpack("<I", position + 24)
        names_rva, = unpack("<I", position + 32)
        if names_count > 4096:
            raise ValidationError("PE 导出数量超限")
        if names_count:
            names_offset, _ = region(names_rva, names_count * 4)
            for index in range(names_count):
                name = text(unpack("<I", names_offset + index * 4)[0])
                if name in exports:
                    raise ValidationError("PE 导出名称重复")
                exports.append(name)
    return {"machine": "x64", "imports": imports(1, 20),
            "delay_imports": imports(13, 32), "exports": sorted(exports)}
