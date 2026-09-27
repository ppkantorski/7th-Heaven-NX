import struct
try:
    import texture2ddecoder as t2d
except ImportError:  # Pillow also decodes the BC1/BC3/BC7 DDS art we ship
    t2d = None

DX10_FORMATS = {
    98: 'BC7',    # DXGI_FORMAT_BC7_UNORM
    99: 'BC7',    # DXGI_FORMAT_BC7_UNORM_SRGB
    95: 'BC6H',
    94: 'BC6H',
    83: 'BC5',
    82: 'BC5',
    80: 'BC4',
    79: 'BC4',
    77: 'BC3',
    78: 'BC3',
    71: 'BC1',
    72: 'BC1',
}


def decode_dds(data):
    if data[:4] != b'DDS ':
        raise ValueError('not a DDS file')
    height, width = struct.unpack_from('<II', data, 12)
    pf_flags, fourcc = struct.unpack_from('<I4s', data, 80)
    hdr_len = 128
    if fourcc == b'DX10':
        dxgi_format = struct.unpack_from('<I', data, 128)[0]
        hdr_len = 148
        kind = DX10_FORMATS.get(dxgi_format)
        if kind is None:
            raise ValueError('unsupported DXGI format %d' % dxgi_format)
    elif fourcc == b'DXT1':
        kind = 'BC1'
    elif fourcc in (b'DXT3',):
        kind = 'BC2'
    elif fourcc in (b'DXT5',):
        kind = 'BC3'
    else:
        raise ValueError('unsupported fourcc %r' % fourcc)

    payload = data[hdr_len:]
    if t2d is None:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            rgba = im.convert('RGBA')
            if rgba.size != (width, height):
                raise ValueError('DDS canvas does not match its header')
            return rgba.tobytes(), width, height
    if kind == 'BC7':
        out = t2d.decode_bc7(payload, width, height)
    elif kind == 'BC1':
        out = t2d.decode_bc1(payload, width, height)
    elif kind == 'BC3':
        out = t2d.decode_bc3(payload, width, height)
    else:
        raise ValueError('decoder not implemented for %s' % kind)

    # texture2ddecoder returns BGRA byte order
    rgba = bytearray(len(out))
    rgba[0::4] = out[2::4]
    rgba[1::4] = out[1::4]
    rgba[2::4] = out[0::4]
    rgba[3::4] = out[3::4]
    return bytes(rgba), width, height
