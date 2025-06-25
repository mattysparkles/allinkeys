// Operation Corned Beef Hashhammer - Left Side Kernel
// Implements RIPEMD-160's left branch (80 rounds)

__constant uint K1[5] = {
    0x00000000, 0x5A827999, 0x6ED9EBA1,
    0x8F1BBCDC, 0xA953FD4E
};

inline uint f1(uint x, uint y, uint z) { return x ^ y ^ z; }
inline uint f2(uint x, uint y, uint z) { return (x & y) | (~x & z); }
inline uint f3(uint x, uint y, uint z) { return (x | ~y) ^ z; }
inline uint f4(uint x, uint y, uint z) { return (x & z) | (y & ~z); }
inline uint f5(uint x, uint y, uint z) { return x ^ (y | ~z); }

inline uint rol(uint x, uint n) { return (x << n) | (x >> (32 - n)); }

__constant uchar R1[80] = {
     0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15,
     7,  4, 13,  1, 10,  6, 15,  3, 12,  0,  9,  5,  2, 14, 11,  8,
     3, 10, 14,  4,  9, 15,  8,  1,  2,  7,  0,  6, 13, 11,  5, 12,
     1,  9, 11, 10,  0,  8, 12,  4, 13,  3,  7, 15, 14,  5,  6,  2,
     4,  0,  5,  9,  7, 12,  2, 10, 14,  1,  3,  8, 11,  6, 15, 13
};

__constant uchar S1[80] = {
    11,14,15,12, 5, 8, 7, 9,11,13,14,15, 6, 7, 9, 8,
     7, 6, 8,13,11, 9, 7,15, 7,12,15, 9,11, 7,13,12,
    11,13, 6, 7,14, 9,13,15,14, 8,13, 6, 5,12, 7, 5,
    11,12,14,15,14,15, 9, 8, 9,14, 5, 6, 8, 6, 5,12,
     9,15, 5,11, 6, 8,13,12, 5,12,13,14,11, 8, 5, 6
};

__kernel void ripemd160_left(__global const uchar *input, __global uint *digest, const int count) {
    int gid = get_global_id(0);
    if (gid >= count) return;

    __local uint X[16]; // message schedule
    int base = gid * 64;
    for (int j = 0; j < 16; ++j) {
        int idx = base + j * 4;
        X[j] = input[idx] |
               (input[idx + 1] << 8) |
               (input[idx + 2] << 16) |
               (input[idx + 3] << 24);
    }

    uint A = 0x67452301;
    uint B = 0xEFCDAB89;
    uint C = 0x98BADCFE;
    uint D = 0x10325476;
    uint E = 0xC3D2E1F0;

    for (int j = 0; j < 80; ++j) {
        uint T, F;
        if (j < 16)        { F = f1(B, C, D); T = K1[0]; }
        else if (j < 32)   { F = f2(B, C, D); T = K1[1]; }
        else if (j < 48)   { F = f3(B, C, D); T = K1[2]; }
        else if (j < 64)   { F = f4(B, C, D); T = K1[3]; }
        else               { F = f5(B, C, D); T = K1[4]; }

        uint r = R1[j];
        uint s = S1[j];
        uint temp = rol(A + F + X[r] + T, s) + E;

        A = E; E = D; D = rol(C, 10); C = B; B = temp;
    }

    // Output resulting A-E (state after left rounds)
    int o = gid * 5;
    digest[o + 0] = A;
    digest[o + 1] = B;
    digest[o + 2] = C;
    digest[o + 3] = D;
    digest[o + 4] = E;
}
