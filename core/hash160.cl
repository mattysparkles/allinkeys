// hash160.cl - Compute SHA256 then RIPEMD160 for public keys
#pragma OPENCL EXTENSION cl_khr_byte_addressable_store : enable

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

uint ROTR(uint x,uint n){return (x>>n)|(x<<(32-n));}
uint Ch(uint x,uint y,uint z){return (x&y)^(~x&z);} 
uint Maj(uint x,uint y,uint z){return (x&y)^(x&z)^(y&z);} 
uint Sigma0(uint x){return ROTR(x,2)^ROTR(x,13)^ROTR(x,22);} 
uint Sigma1(uint x){return ROTR(x,6)^ROTR(x,11)^ROTR(x,25);} 
uint sigma0(uint x){return ROTR(x,7)^ROTR(x,18)^(x>>3);} 
uint sigma1(uint x){return ROTR(x,17)^ROTR(x,19)^(x>>10);} 

inline uint rol(uint x, uint n){return (x<<n)|(x>>(32-n));}
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

__kernel void hash160(__global const uchar *inputs, __global uchar *outputs, const uint input_size){
    const uint gid=get_global_id(0);
    __global const uchar *in=inputs+gid*input_size;

    // ---- SHA256 ----
    uchar msg[128];
    uint len=input_size;
    int i;
    for(i=0;i<len;i++) msg[i]=in[i];
    msg[len]=0x80;
    uint total=((len+9+63)/64)*64;
    for(i=len+1;i<total;i++) msg[i]=0;
    ulong bitlen=(ulong)len*8UL;
    for(i=0;i<8;i++) msg[total-1-i]=(uchar)(bitlen>>(8*i));

    uint h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,
         h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
    uint w[64];
    for(uint block=0;block<total;block+=64){
        for(i=0;i<16;i++){
            int j=block+i*4;
            w[i]=((uint)msg[j]<<24)|((uint)msg[j+1]<<16)|((uint)msg[j+2]<<8)|((uint)msg[j+3]);
        }
        for(i=16;i<64;i++) w[i]=sigma1(w[i-2])+w[i-7]+sigma0(w[i-15])+w[i-16];
        uint a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,h=h7;
        for(i=0;i<64;i++){
            uint t1=h+Sigma1(e)+Ch(e,f,g)+k[i]+w[i];
            uint t2=Sigma0(a)+Maj(a,b,c);
            h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        h0+=a;h1+=b;h2+=c;h3+=d;h4+=e;h5+=f;h6+=g;h7+=h;
    }
    uchar sha_out[32];
    uint hs[8]={h0,h1,h2,h3,h4,h5,h6,h7};
    for(i=0;i<8;i++){sha_out[i*4]=(uchar)(hs[i]>>24);sha_out[i*4+1]=(uchar)(hs[i]>>16);sha_out[i*4+2]=(uchar)(hs[i]>>8);sha_out[i*4+3]=(uchar)hs[i];}

    // ---- RIPEMD160 ----
    uchar m2[64];
    for(i=0;i<32;i++) m2[i]=sha_out[i];
    m2[32]=0x80; for(i=33;i<56;i++) m2[i]=0;
    ulong bl2=256UL;
    for(i=0;i<8;i++) m2[63-i]=(uchar)(bl2>>(8*i));

    uint X[16];
    for(i=0;i<16;i++){
        int j=i*4; X[i]=((uint)m2[j])|((uint)m2[j+1]<<8)|((uint)m2[j+2]<<16)|((uint)m2[j+3]<<24);
    }

    uint al=0x67452301,bl=0xEFCDAB89,cl=0x98BADCFE,dl=0x10325476,el=0xC3D2E1F0;
    uint ar=0x76543210,br=0xFEDCBA98,cr=0x89ABCDEF,dr=0x01234567,er=0x3C2D1E0F;

    for(i=0;i<80;i++){
        uint tl,tr;
        uint rl=R1[i],sl=S1[i];
        if(i<16){tl=rol(al+f1(bl,cl,dl)+X[rl]+K1[0],sl)+el;}
        else if(i<32){tl=rol(al+f2(bl,cl,dl)+X[rl]+K1[1],sl)+el;}
        else if(i<48){tl=rol(al+f3(bl,cl,dl)+X[rl]+K1[2],sl)+el;}
        else if(i<64){tl=rol(al+f4(bl,cl,dl)+X[rl]+K1[3],sl)+el;}
        else{tl=rol(al+f5(bl,cl,dl)+X[rl]+K1[4],sl)+el;}
        al=el;el=dl;dl=rol(cl,10);cl=bl;bl=tl;

        rl=R2[i];sl=S2[i];
        if(i<16){tr=rol(ar+f5(br,cr,dr)+X[rl]+K2[0],sl)+er;}
        else if(i<32){tr=rol(ar+f4(br,cr,dr)+X[rl]+K2[1],sl)+er;}
        else if(i<48){tr=rol(ar+f3(br,cr,dr)+X[rl]+K2[2],sl)+er;}
        else if(i<64){tr=rol(ar+f2(br,cr,dr)+X[rl]+K2[3],sl)+er;}
        else{tr=rol(ar+f1(br,cr,dr)+X[rl]+K2[4],sl)+er;}
        ar=er;er=dr;dr=rol(cr,10);cr=br;br=tr;
    }
    uint h0r=0x67452301,h1r=0xEFCDAB89,h2r=0x98BADCFE,h3r=0x10325476,h4r=0xC3D2E1F0;
    uint T=h1r+cl+dr;
    h1r=h2r+dl+er;
    h2r=h3r+el+ar;
    h3r=h4r+al+br;
    h4r=h0r+bl+cr;
    h0r=T;

    __global uchar *out=outputs+gid*20;
    uint hh[5]={h0r,h1r,h2r,h3r,h4r};
    for(i=0;i<5;i++){
        out[i*4]=(uchar)(hh[i]);
        out[i*4+1]=(uchar)(hh[i]>>8);
        out[i*4+2]=(uchar)(hh[i]>>16);
        out[i*4+3]=(uchar)(hh[i]>>24);
    }
}
