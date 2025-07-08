__kernel void ripemd160_combine(
    __global const uint *left,
    __global const uint *right,
    __global uchar *out,
    const int count)
{
    int gid = get_global_id(0);
    if (gid >= count) return;

    const uint h0_init = 0x67452301;
    const uint h1_init = 0xEFCDAB89;
    const uint h2_init = 0x98BADCFE;
    const uint h3_init = 0x10325476;
    const uint h4_init = 0xC3D2E1F0;

    int idx = gid * 5;
    uint A1 = left[idx + 0];
    uint B1 = left[idx + 1];
    uint C1 = left[idx + 2];
    uint D1 = left[idx + 3];
    uint E1 = left[idx + 4];

    uint A2 = right[idx + 0];
    uint B2 = right[idx + 1];
    uint C2 = right[idx + 2];
    uint D2 = right[idx + 3];
    uint E2 = right[idx + 4];

    uint h0 = h0_init;
    uint h1 = h1_init;
    uint h2 = h2_init;
    uint h3 = h3_init;
    uint h4 = h4_init;

    uint T = h1 + C1 + D2;
    h1 = h2 + D1 + E2;
    h2 = h3 + E1 + A2;
    h3 = h4 + A1 + B2;
    h4 = h0 + B1 + C2;
    h0 = T;

    __global uchar *o = out + gid * 20;
    o[0] = (uchar)(h0);
    o[1] = (uchar)(h0 >> 8);
    o[2] = (uchar)(h0 >> 16);
    o[3] = (uchar)(h0 >> 24);
    o[4] = (uchar)(h1);
    o[5] = (uchar)(h1 >> 8);
    o[6] = (uchar)(h1 >> 16);
    o[7] = (uchar)(h1 >> 24);
    o[8] = (uchar)(h2);
    o[9] = (uchar)(h2 >> 8);
    o[10] = (uchar)(h2 >> 16);
    o[11] = (uchar)(h2 >> 24);
    o[12] = (uchar)(h3);
    o[13] = (uchar)(h3 >> 8);
    o[14] = (uchar)(h3 >> 16);
    o[15] = (uchar)(h3 >> 24);
    o[16] = (uchar)(h4);
    o[17] = (uchar)(h4 >> 8);
    o[18] = (uchar)(h4 >> 16);
    o[19] = (uchar)(h4 >> 24);
}