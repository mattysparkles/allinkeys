/*
 * hash160.cl - AMD-optimized HASH160 (SHA-256 + RIPEMD-160) kernels.
 *
 * Kernels provided:
 *   hash160        - compute HASH160 for each input public key and write
 *                    20-byte digests to the output buffer.
 *   hash160_prefix - compute HASH160 and compare the first prefix_len bytes
 *                    against a supplied prefix; writes 0/1 match flags.
 *
 * Highlights:
 *   - Specialised fast paths for 33-byte (compressed) and 65-byte
 *     (uncompressed) secp256k1 public keys with direct register assembly.
 *   - SHA-256 schedule uses a 16-word circular buffer (W[t & 15]); no
 *     msg[128] or W[64] arrays.  Greatly reduces VGPR usage on RDNA.
 *   - Vectorised global I/O via vload4/vstore4 where alignment permits.
 *   - Targeted unrolling (#pragma unroll 8 for SHA, 5 for RIPEMD) and
 *     rotate() builtin on AMD for RIPEMD rounds.
 *   - NVIDIA users continue to use core/hash160_nvidia.cl; host code selects
 *     kernel based on device vendor.
 *
 * Buffer layout:
 *   inputs  - input_size bytes per work-item.
 *   outputs - 20 bytes per work-item (hash160 digest) for hash160.
 *   prefix  - byte prefix to match for hash160_prefix.
 *   matches - uint per work-item (0/1) for hash160_prefix.
 */

#pragma OPENCL EXTENSION cl_khr_byte_addressable_store : enable

#if defined(cl_amd_media_ops) || defined(__AMD__)
#define AMD_PATH 1
#else
#define AMD_PATH 0
#endif

/* --- SHA-256 constants and helpers --- */
__constant uint k[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

inline uint ROTR(uint x,uint n){return (x>>n)|(x<<(32-n));}
inline uint Ch(uint x,uint y,uint z){return (x&y)^(~x&z);} 
inline uint Maj(uint x,uint y,uint z){return (x&y)^(x&z)^(y&z);} 
inline uint Sigma0(uint x){return ROTR(x,2)^ROTR(x,13)^ROTR(x,22);} 
inline uint Sigma1(uint x){return ROTR(x,6)^ROTR(x,11)^ROTR(x,25);} 
inline uint sigma0(uint x){return ROTR(x,7)^ROTR(x,18)^(x>>3);} 
inline uint sigma1(uint x){return ROTR(x,17)^ROTR(x,19)^(x>>10);} 
inline uint bswap32(uint x){return rotate(x,8)&0x00FF00FF | rotate(x,24)&0xFF00FF00;}

/* --- RIPEMD-160 constants and helpers --- */
#if AMD_PATH
#define ROL(x,n) rotate((uint)(x),(uint)(n))
#else
inline uint ROL(uint x,uint n){return (x<<n)|(x>>(32-n));}
#endif
inline uint f1(uint x,uint y,uint z){return x^y^z;}
inline uint f2(uint x,uint y,uint z){return (x&y)|(~x&z);} 
inline uint f3(uint x,uint y,uint z){return (x|~y)^z;} 
inline uint f4(uint x,uint y,uint z){return (x&z)|(y&~z);} 
inline uint f5(uint x,uint y,uint z){return x^(y|~z);} 

__constant uchar R1[80]={
 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
 7,4,13,1,10,6,15,3,12,0,9,5,2,14,11,8,
 3,10,14,4,9,15,8,1,2,7,0,6,13,11,5,12,
 1,9,11,10,0,8,12,4,13,3,7,15,14,5,6,2,
 4,0,5,9,7,12,2,10,14,1,3,8,11,6,15,13};

__constant uchar R2[80]={
 5,14,7,0,9,2,11,4,13,6,15,8,1,10,3,12,
 6,11,3,7,0,13,5,10,14,15,8,12,4,9,1,2,
 15,5,1,3,7,14,6,9,11,8,12,2,10,0,4,13,
 8,6,4,1,3,11,15,0,5,12,2,13,9,7,10,14,
 12,15,10,4,1,5,8,7,6,2,13,14,0,3,9,11};

__constant uchar S1[80]={
 11,14,15,12,5,8,7,9,11,13,14,15,6,7,9,8,
 7,6,8,13,11,9,7,15,7,12,15,9,11,7,13,12,
 11,13,6,7,14,9,13,15,14,8,13,6,5,12,7,5,
 11,12,14,15,14,15,9,8,9,14,5,6,8,6,5,12,
 9,15,5,11,6,8,13,12,5,12,13,14,11,8,5,6};

__constant uchar S2[80]={
 8,9,9,11,13,15,15,5,7,7,8,11,14,14,12,6,
 9,13,15,7,12,8,9,11,7,7,12,7,6,15,13,11,
 9,7,15,11,8,6,6,14,12,13,5,14,13,13,7,5,
 15,5,8,11,14,14,6,14,6,9,12,9,12,5,15,8,
 8,5,12,9,12,5,14,6,8,13,6,5,15,13,11,11};

__constant uint K1[5]={0x00000000,0x5A827999,0x6ED9EBA1,0x8F1BBCDC,0xA953FD4E};
__constant uint K2[5]={0x50A28BE6,0x5C4DD124,0x6D703EF3,0x7A6D76E9,0x00000000};

/* --- SHA-256 block compression with circular schedule --- */
inline void sha256_compress(uint W[16], uint h[8]){
    uint a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
    #pragma unroll 8
    for(uint t=0;t<64;++t){
        uint Wt;
        if(t<16) Wt=W[t];
        else{
            uint s0=sigma0(W[(t-15)&15]);
            uint s1=sigma1(W[(t-2)&15]);
            Wt=W[(t-16)&15]+s0+W[(t-7)&15]+s1;
            W[t&15]=Wt;
        }
        uint T1=hh+Sigma1(e)+Ch(e,f,g)+k[t]+Wt;
        uint T2=Sigma0(a)+Maj(a,b,c);
        hh=g; g=f; f=e; e=d+T1; d=c; c=b; b=a; a=T1+T2;
    }
    h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=hh;
}

/* --- HASH160 core: SHA-256 followed by RIPEMD-160 --- */
inline void hash160_core(__global const uchar *in, uint len, uint digest[5]){
    uint h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    uint W[16];
    if(len==33){
        uint4 v0=vload4(0,(__global const uint*)in);
        uint4 v1=vload4(1,(__global const uint*)in);
        W[0]=bswap32(v0.s0); W[1]=bswap32(v0.s1); W[2]=bswap32(v0.s2); W[3]=bswap32(v0.s3);
        W[4]=bswap32(v1.s0); W[5]=bswap32(v1.s1); W[6]=bswap32(v1.s2); W[7]=bswap32(v1.s3);
        W[8]=((uint)in[32]<<24)|0x800000u;
        for(int i=9;i<15;i++) W[i]=0;
        W[15]=len*8u;
        sha256_compress(W,h);
    }else if(len==65){
        const __global uint* ptr=(const __global uint*)in;
        for(int i=0;i<16;i+=4){
            uint4 vv=vload4(i/4,ptr);
            W[i]=bswap32(vv.s0); W[i+1]=bswap32(vv.s1);
            W[i+2]=bswap32(vv.s2); W[i+3]=bswap32(vv.s3);
        }
        sha256_compress(W,h);
        W[0]=((uint)in[64]<<24)|0x800000u;
        for(int i=1;i<15;i++) W[i]=0;
        W[15]=len*8u;
        sha256_compress(W,h);
    }else{
        ulong bitlen=(ulong)len*8UL;
        uint blocks=((len+9+63)>>6);
        for(uint b=0;b<blocks;++b){
            for(uint i=0;i<16;++i){
                uint j=b*64+i*4; uint w=0; 
                for(int k=0;k<4;++k){
                    uint idx=j+k; uint c=0;
                    if(idx<len) c=in[idx];
                    else if(idx==len) c=0x80;
                    else if(idx>=blocks*64-8) c=(uint)(bitlen>>((blocks*64-1-idx)*8));
                    w=(w<<8)|c;
                }
                W[i]=w;
            }
            sha256_compress(W,h);
        }
    }
    uint X[16];
    for(int i=0;i<8;i++) X[i]=bswap32(h[i]);
    X[8]=0x00000080u; for(int i=9;i<14;i++) X[i]=0; X[14]=256; X[15]=0;
    uint al=0x67452301,bl=0xEFCDAB89,cl=0x98BADCFE,dl=0x10325476,el=0xC3D2E1F0;
    uint ar=0x76543210,br=0xFEDCBA98,cr=0x89ABCDEF,dr=0x01234567,er=0x3C2D1E0F;
    #pragma unroll 5
    for(uint i=0;i<80;i++){
        uint rl=R1[i],sl=S1[i],tl;
        if(i<16) tl=ROL(al+f1(bl,cl,dl)+X[rl]+K1[0],sl)+el;
        else if(i<32) tl=ROL(al+f2(bl,cl,dl)+X[rl]+K1[1],sl)+el;
        else if(i<48) tl=ROL(al+f3(bl,cl,dl)+X[rl]+K1[2],sl)+el;
        else if(i<64) tl=ROL(al+f4(bl,cl,dl)+X[rl]+K1[3],sl)+el;
        else tl=ROL(al+f5(bl,cl,dl)+X[rl]+K1[4],sl)+el;
        al=el; el=dl; dl=ROL(cl,10); cl=bl; bl=tl;
        uint rr=R2[i],sr=S2[i],tr;
        if(i<16) tr=ROL(ar+f5(br,cr,dr)+X[rr]+K2[0],sr)+er;
        else if(i<32) tr=ROL(ar+f4(br,cr,dr)+X[rr]+K2[1],sr)+er;
        else if(i<48) tr=ROL(ar+f3(br,cr,dr)+X[rr]+K2[2],sr)+er;
        else if(i<64) tr=ROL(ar+f2(br,cr,dr)+X[rr]+K2[3],sr)+er;
        else tr=ROL(ar+f1(br,cr,dr)+X[rr]+K2[4],sr)+er;
        ar=er; er=dr; dr=ROL(cr,10); cr=br; br=tr;
    }
    uint h0=0x67452301,h1=0xEFCDAB89,h2=0x98BADCFE,h3=0x10325476,h4=0xC3D2E1F0;
    uint T=h1+cl+dr; h1=h2+dl+er; h2=h3+el+ar; h3=h4+al+br; h4=h0+bl+cr; h0=T;
    digest[0]=h0; digest[1]=h1; digest[2]=h2; digest[3]=h3; digest[4]=h4;
}

/* --- Kernels --- */
__kernel void hash160(__global const uchar* inputs, __global uchar* outputs, const uint input_size){
    uint gid=get_global_id(0);
    __global const uchar* in=inputs+gid*input_size;
    uint d[5];
    hash160_core(in,input_size,d);
    __global uchar* out=outputs+gid*20;
    vstore4((uint4)(d[0],d[1],d[2],d[3]),0,(__global uint*)out);
    ((__global uint*)out)[4]=d[4];
}

__kernel void hash160_prefix(__global const uchar* inputs, uint input_size,
                             __global const uchar* prefix, uint prefix_len,
                             __global uint* matches){
    uint gid=get_global_id(0);
    __global const uchar* in=inputs+gid*input_size;
    uint d[5];
    hash160_core(in,input_size,d);
    __private uchar dig[20];
    vstore4((uint4)(d[0],d[1],d[2],d[3]),0,(__private uint*)dig);
    *((__private uint*)(dig+16))=d[4];
    uint m=1;
    for(uint i=0;i<prefix_len;i++) if(dig[i]!=prefix[i]){m=0;break;}
    matches[gid]=m;
}

