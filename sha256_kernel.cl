// sha256_kernel.cl

#pragma OPENCL EXTENSION cl_khr_byte_addressable_store : enable

__constant uint k[64] = {
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

uint ROTR(uint x, uint n) {
  return (x >> n) | (x << (32 - n));
}

uint Ch(uint x, uint y, uint z) {
  return (x & y) ^ (~x & z);
}

uint Maj(uint x, uint y, uint z) {
  return (x & y) ^ (x & z) ^ (y & z);
}

uint Sigma0(uint x) {
  return ROTR(x, 2) ^ ROTR(x, 13) ^ ROTR(x, 22);
}

uint Sigma1(uint x) {
  return ROTR(x, 6) ^ ROTR(x, 11) ^ ROTR(x, 25);
}

uint sigma0(uint x) {
  return ROTR(x, 7) ^ ROTR(x, 18) ^ (x >> 3);
}

uint sigma1(uint x) {
  return ROTR(x, 17) ^ ROTR(x, 19) ^ (x >> 10);
}

__kernel void sha256_batch(__global const uchar *inputs, __global uchar *outputs, uint input_size) {
  int gid = get_global_id(0);
  __global const uchar *data = inputs + gid * input_size;
  __global uchar *digest = outputs + gid * 32;

  // SHA-256 initial hash values
  uint h[8] = {
    0x6a09e667,
    0xbb67ae85,
    0x3c6ef372,
    0xa54ff53a,
    0x510e527f,
    0x9b05688c,
    0x1f83d9ab,
    0x5be0cd19
  };

  uint w[64];
  for (int i = 0; i < 16; i++) {
    int j = i * 4;
    w[i] = (uint)data[j] << 24 | (uint)data[j + 1] << 16 | (uint)data[j + 2] << 8 | (uint)data[j + 3];
  }

  // Padding single block (assumes 32-byte input max)
  w[8] = 0x80000000;  // 1 bit then zeros
  for (int i = 9; i < 15; i++) w[i] = 0x00000000;
  w[15] = input_size * 8;

  for (int i = 16; i < 64; i++) {
    w[i] = sigma1(w[i - 2]) + w[i - 7] + sigma0(w[i - 15]) + w[i - 16];
  }

  uint a = h[0], b = h[1], c = h[2], d = h[3];
  uint e = h[4], f = h[5], g = h[6], h0 = h[7];

  for (int i = 0; i < 64; i++) {
    uint t1 = h0 + Sigma1(e) + Ch(e, f, g) + k[i] + w[i];
    uint t2 = Sigma0(a) + Maj(a, b, c);
    h0 = g;
    g = f;
    f = e;
    e = d + t1;
    d = c;
    c = b;
    b = a;
    a = t1 + t2;
  }

  h[0] += a; h[1] += b; h[2] += c; h[3] += d;
  h[4] += e; h[5] += f; h[6] += g; h[7] += h0;

  for (int i = 0; i < 8; i++) {
    digest[i * 4 + 0] = (uchar)(h[i] >> 24);
    digest[i * 4 + 1] = (uchar)(h[i] >> 16);
    digest[i * 4 + 2] = (uchar)(h[i] >> 8);
    digest[i * 4 + 3] = (uchar)(h[i]);
  }
}
