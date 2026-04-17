"""
Zhihu ZSE v4 Encryption/Decryption Module
Based on the Rust implementation from repository: https://github.com/zly2006/zhihu-plus-plus/tree/master/rs-zse-sign/
"""

import struct
from typing import Optional


class ZSECipher:
    """Zhihu ZSE v4 cipher implementation"""
    
    # Constants from the Rust code
    ZK = [
        1170614578, 1024848638, 1413669199, 3951632832, 3528873006, 2921909214,
        4151847688, 3997739139, 1933479194, 3323781115, 3888513386, 460404854,
        3747539722, 2403641034, 2615871395, 2119585428, 2265697227, 2035090028,
        2773447226, 4289380121, 4217216195, 2200601443, 3051914490, 1579901135,
        1321810770, 456816404, 2903323407, 4065664991, 330002838, 3506006750,
        363569021, 2347096187,
    ]
    
    ZB = [
        20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91,
        237, 9, 125, 157, 230, 93, 252, 205, 90, 79, 144, 199, 159, 197, 186,
        167, 39, 37, 156, 198, 38, 42, 43, 168, 217, 153, 15, 103, 80, 189, 71,
        191, 97, 84, 247, 95, 36, 69, 14, 35, 12, 171, 28, 114, 178, 148, 86,
        182, 32, 83, 158, 109, 22, 255, 94, 238, 151, 85, 77, 124, 254, 18, 4,
        26, 123, 176, 232, 193, 131, 172, 143, 142, 150, 30, 10, 146, 162, 62,
        224, 218, 196, 229, 1, 192, 213, 27, 110, 56, 231, 180, 138, 107, 242,
        187, 54, 120, 19, 44, 117, 228, 215, 203, 53, 239, 251, 127, 81, 11,
        133, 96, 204, 132, 41, 115, 73, 55, 249, 147, 102, 48, 122, 145, 106,
        118, 74, 190, 29, 16, 174, 5, 177, 129, 63, 113, 99, 31, 161, 76, 246,
        34, 211, 13, 60, 68, 207, 160, 65, 111, 82, 165, 67, 169, 225, 57, 112,
        244, 155, 51, 236, 200, 233, 58, 61, 47, 100, 137, 185, 64, 17, 70, 234,
        163, 219, 108, 170, 166, 59, 149, 52, 105, 24, 212, 78, 173, 45, 0, 116,
        226, 119, 136, 206, 135, 175, 195, 25, 92, 121, 208, 126, 139, 3, 75,
        141, 21, 130, 98, 241, 40, 154, 66, 184, 49, 181, 46, 243, 88, 101, 183,
        8, 23, 72, 188, 104, 179, 210, 134, 250, 201, 164, 89, 216, 202, 220,
        50, 221, 152, 140, 33, 235, 214,
    ]
    
    ALPHABET = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
    KEY16 = b"059053f7d15e01d7"
    
    @classmethod
    def _read_u32_be(cls, data: bytes, offset: int) -> int:
        """Read a big-endian 32-bit integer from bytes"""
        return struct.unpack('>I', data[offset:offset + 4])[0]
    
    @classmethod
    def _write_u32_be(cls, value: int) -> bytes:
        """Write a big-endian 32-bit integer to bytes"""
        return struct.pack('>I', value)
    
    @classmethod
    def _g_transform(cls, tt: int) -> int:
        """G transformation function"""
        te = tt.to_bytes(4, 'big')
        tr = bytes([
            cls.ZB[te[0]],
            cls.ZB[te[1]],
            cls.ZB[te[2]],
            cls.ZB[te[3]],
        ])
        ti = int.from_bytes(tr, 'big')
        return ti ^ cls._rotl(ti, 2) ^ cls._rotl(ti, 10) ^ cls._rotl(ti, 18) ^ cls._rotl(ti, 24)
    
    @staticmethod
    def _rotl(x: int, n: int) -> int:
        """Rotate left a 32-bit integer"""
        n %= 32
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF
    
    @classmethod
    def _r_block(cls, input16: bytes) -> bytes:
        """Block encryption (forward)"""
        tr = [0] * 36
        tr[0] = cls._read_u32_be(input16, 0)
        tr[1] = cls._read_u32_be(input16, 4)
        tr[2] = cls._read_u32_be(input16, 8)
        tr[3] = cls._read_u32_be(input16, 12)
        
        for i in range(32):
            ta = cls._g_transform(tr[i + 1] ^ tr[i + 2] ^ tr[i + 3] ^ cls.ZK[i])
            tr[i + 4] = tr[i] ^ ta
        
        result = bytearray()
        result.extend(cls._write_u32_be(tr[35]))
        result.extend(cls._write_u32_be(tr[34]))
        result.extend(cls._write_u32_be(tr[33]))
        result.extend(cls._write_u32_be(tr[32]))
        return bytes(result)
    
    @classmethod
    def _r_block_decrypt(cls, input16: bytes) -> bytes:
        """Block decryption (reverse)"""
        tr = [0] * 36
        tr[0] = cls._read_u32_be(input16, 0)
        tr[1] = cls._read_u32_be(input16, 4)
        tr[2] = cls._read_u32_be(input16, 8)
        tr[3] = cls._read_u32_be(input16, 12)
        
        for i in range(32):
            ta = cls._g_transform(tr[i + 1] ^ tr[i + 2] ^ tr[i + 3] ^ cls.ZK[31 - i])
            tr[i + 4] = tr[i] ^ ta
        
        result = bytearray()
        result.extend(cls._write_u32_be(tr[35]))
        result.extend(cls._write_u32_be(tr[34]))
        result.extend(cls._write_u32_be(tr[33]))
        result.extend(cls._write_u32_be(tr[32]))
        return bytes(result)
    
    @classmethod
    def _x_blocks(cls, data: bytes, iv: bytes) -> bytes:
        """XOR and encrypt blocks in CBC mode"""
        if not data:
            return b''
        
        out = bytearray()
        current_iv = bytearray(iv)
        
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            mixed = bytes([chunk[j] ^ current_iv[j] for j in range(len(chunk))])
            current_iv = bytearray(cls._r_block(mixed))
            out.extend(current_iv)
        
        return bytes(out)
    
    @classmethod
    def _encode_uri_component(cls, text: str) -> bytes:
        """Encode a string as a URI component"""
        def is_unescaped(b: int) -> bool:
            return (
                (ord('A') <= b <= ord('Z')) or
                (ord('a') <= b <= ord('z')) or
                (ord('0') <= b <= ord('9')) or
                b in (ord('-'), ord('_'), ord('.'), ord('!'), ord('~'),
                      ord('*'), ord("'"), ord('('), ord(')'))
            )
        
        result = bytearray()
        for b in text.encode('utf-8'):
            if is_unescaped(b):
                result.append(b)
            else:
                result.append(ord('%'))
                result.append(ord("0123456789ABCDEF"[b >> 4]))
                result.append(ord("0123456789ABCDEF"[b & 0x0F]))
        return bytes(result)
    
    @classmethod
    def _decode_uri_component(cls, data: bytes) -> bytes:
        """Decode a URI component"""
        def hex_val(b: int) -> Optional[int]:
            if ord('0') <= b <= ord('9'):
                return b - ord('0')
            elif ord('a') <= b <= ord('f'):
                return b - ord('a') + 10
            elif ord('A') <= b <= ord('F'):
                return b - ord('A') + 10
            return None
        
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ord('%'):
                if i + 2 >= len(data):
                    raise ValueError("Invalid percent encoding")
                h = hex_val(data[i + 1])
                l = hex_val(data[i + 2])
                if h is None or l is None:
                    raise ValueError("Invalid percent encoding")
                result.append((h << 4) | l)
                i += 3
            else:
                result.append(data[i])
                i += 1
        return bytes(result)
    
    @classmethod
    def _custom_encode(cls, bytes_data: bytes) -> str:
        """Custom encoding to the specific alphabet"""
        data = bytearray(bytes_data)
        # Pad to multiple of 3
        while len(data) % 3 != 0:
            data.append(0)
        
        alphabet_bytes = cls.ALPHABET.encode('ascii')
        result = []
        i = 0
        p = len(data) - 1
        
        while p >= 0:
            v = 0
            
            b0 = data[p]
            m0 = (58 >> (8 * (i % 4))) & 0xFF
            i += 1
            v |= (b0 ^ m0) & 0xFF
            
            b1 = data[p - 1]
            m1 = (58 >> (8 * (i % 4))) & 0xFF
            i += 1
            v |= ((b1 ^ m1) & 0xFF) << 8
            
            b2 = data[p - 2]
            m2 = (58 >> (8 * (i % 4))) & 0xFF
            i += 1
            v |= ((b2 ^ m2) & 0xFF) << 16
            
            result.append(chr(alphabet_bytes[v & 63]))
            result.append(chr(alphabet_bytes[(v >> 6) & 63]))
            result.append(chr(alphabet_bytes[(v >> 12) & 63]))
            result.append(chr(alphabet_bytes[(v >> 18) & 63]))
            
            p -= 3
        
        return ''.join(result)
    
    @classmethod
    def _decode_custom(cls, encoded: str) -> bytes:
        """Decode from the custom encoding"""
        if len(encoded) % 4 != 0:
            raise ValueError("Invalid encoded length")
        
        # Build reverse mapping
        reverse = [255] * 128
        for i, ch in enumerate(cls.ALPHABET):
            reverse[ord(ch)] = i
        
        processed = bytearray()
        i = 0
        bytes_data = encoded.encode('ascii')
        p = 0
        
        while p < len(bytes_data):
            a, b, c, d = bytes_data[p:p + 4]
            p += 4
            
            if a >= 128 or b >= 128 or c >= 128 or d >= 128:
                raise ValueError("Invalid alphabet char")
            
            ia, ib, ic, id = reverse[a], reverse[b], reverse[c], reverse[d]
            if 255 in (ia, ib, ic, id):
                raise ValueError("Invalid alphabet char")
            
            v = ia | (ib << 6) | (ic << 12) | (id << 18)
            for shift in (0, 8, 16):
                x = (v >> shift) & 0xFF
                mask = (58 >> (8 * (i % 4))) & 0xFF
                i += 1
                processed.append(x ^ mask)
        
        processed.reverse()
        trim = len(processed) % 16
        if trim > 2 or len(processed) < trim:
            raise ValueError("Invalid block alignment")
        
        processed = processed[:len(processed) - trim]
        if not processed or len(processed) % 16 != 0:
            raise ValueError("Invalid ciphertext length")
        
        return bytes(processed)
    
    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        """Remove PKCS7 padding"""
        if not data:
            raise ValueError("Empty plaintext")
        
        pad = data[-1]
        if pad == 0 or pad > 16 or pad > len(data):
            raise ValueError("Invalid PKCS7 padding")
        
        if not all(b == pad for b in data[-pad:]):
            raise ValueError("Invalid PKCS7 padding")
        
        return data[:-pad]
    
    def encrypt(self, text: str) -> str:
        """Encrypt a string using ZSE v4"""
        seed = 12  # matches the Rust implementation
        
        # Build plaintext
        plain = bytearray()
        plain.append(seed)
        plain.append(0)
        plain.extend(self._encode_uri_component(text))
        
        # Add PKCS7 padding
        pad = 16 - (len(plain) % 16)
        plain.extend([pad] * pad)
        
        # First block XOR with key and 42
        first = bytearray(16)
        for i in range(16):
            first[i] = plain[i] ^ self.KEY16[i] ^ 42
        
        # Encrypt
        c0 = self._r_block(bytes(first))
        cipher = bytearray(c0)
        
        if len(plain) > 16:
            cipher.extend(self._x_blocks(bytes(plain[16:]), c0))
        
        return self._custom_encode(bytes(cipher))
    
    def decrypt(self, encoded: str) -> str:
        """Decrypt a ZSE v4 encrypted string"""
        cipher = self._decode_custom(encoded)
        
        # Decrypt first block
        first = self._r_block_decrypt(cipher[:16])
        plain = bytearray()
        for i in range(16):
            plain.append(first[i] ^ self.KEY16[i] ^ 42)
        
        # Decrypt remaining blocks
        prev_block = cipher[:16]
        for i in range(16, len(cipher), 16):
            chunk = cipher[i:i + 16]
            dec = self._r_block_decrypt(chunk)
            for j in range(16):
                plain.append(dec[j] ^ prev_block[j])
            prev_block = chunk
        
        # Remove padding and validate
        plain = self._pkcs7_unpad(bytes(plain))
        
        if len(plain) < 2 or plain[1] != 0:
            raise ValueError("Invalid plaintext header")
        
        # Decode URI component
        raw = self._decode_uri_component(plain[2:])
        return raw.decode('utf-8')


def main():
    """CLI interface for the ZSE cipher"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python zse.py <input>")
        print("  Automatically detects whether to encrypt or decrypt")
        sys.exit(1)
    
    cipher = ZSECipher()
    input_text = sys.argv[1]
    
    try:
        # Try to decrypt first
        decrypted = cipher.decrypt(input_text)
        print(f"decrypted: {decrypted}")
    except Exception:
        # If decryption fails, encrypt
        encrypted = cipher.encrypt(input_text)
        print(encrypted)


if __name__ == "__main__":
    main()