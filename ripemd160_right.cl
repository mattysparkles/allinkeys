__kernel void ripemd160_right(
    __global const uchar *input,
    __global uint *output,
    const uint input_offset,
    const uint total_blocks)
{
    const uint idx = get_global_id(0);
    if (idx >= total_blocks) return;

    __global const uchar *data = input + (idx + input_offset) * 64;

    // Load 16 32-bit words (little-endian)
    uint x[16];
    for (int i = 0; i < 16; i++) {
        x[i] = ((uint)data[i * 4]) |
               ((uint)data[i * 4 + 1] << 8) |
               ((uint)data[i * 4 + 2] << 16) |
               ((uint)data[i * 4 + 3] << 24);
    }

    // Initial hash values
    uint a = 0x76543210;
    uint b = 0xFEDCBA98;
    uint c = 0x89ABCDEF;
    uint d = 0x01234567;
    uint e = 0x3C2D1E0F;

    // Right line rotation amounts and message schedule
    const uchar r[80] = {
        5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
        6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
        15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
        8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
        12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11
    };

    const uchar s[80] = {
        8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
        9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
        9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
        15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
        8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11
    };

    // Constants for right path
    const uint K[5] = { 0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000 };

    // Boolean functions for each round
    #define f1(x, y, z) ((x) ^ (y) ^ (z))
    #define f2(x, y, z) (((x) & (z)) | ((y) & ~(z)))
    #define f3(x, y, z) (((x) | ~(y)) ^ (z))
    #define f4(x, y, z) (((x) & (y)) | ((~x) & (z)))
    #define f5(x, y, z) ((x) ^ ((y) | ~(z)))

    for (int j = 0; j < 80; j++) {
        uint f;
        uint k;
        if (j < 16)      { f = f1(b, c, d); k = K[0]; }
        else if (j < 32) { f = f2(b, c, d); k = K[1]; }
        else if (j < 48) { f = f3(b, c, d); k = K[2]; }
        else if (j < 64) { f = f4(b, c, d); k = K[3]; }
        else             { f = f5(b, c, d); k = K[4]; }

        uint t = a + f + x[r[j]] + k;
        t = rotate(t, s[j]) + e;
        a = e; e = d; d = rotate(c, 10); c = b; b = t;
    }

    // Store final state to output
    output[idx * 5 + 0] = a;
    output[idx * 5 + 1] = b;
    output[idx * 5 + 2] = c;
    output[idx * 5 + 3] = d;
    output[idx * 5 + 4] = e;
}
