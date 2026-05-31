# This file has been duplicated to prevent Unicode/Charmap issues under Windows CLI execution.
# File is kept fully identical in algorithm but removes Unicode symbols (e.g. →, ✓, ✗) from all print blocks.


import os
import sys
import time
import json
import queue
import hashlib
import argparse
import threading
import multiprocessing as mp
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import numpy as np
import subprocess
import tempfile
import re

# ── GPU ──────────────────────────────────────────────────────────────────────
GPU_AVAILABLE = False
GPU_IMPORT_ERROR: Optional[str] = None

try:
    import cupy as cp
    _test = cp.cuda.Device(0).use()
    _arr  = cp.zeros(1024, dtype=cp.uint8)
    del _arr
    GPU_AVAILABLE = True
except Exception as _e:
    GPU_IMPORT_ERROR = str(_e)

# ── RICH ─────────────────────────────────────────────────────────────────────
from rich.live     import Live
from rich.table    import Table
from rich.panel    import Panel
from rich.console  import Console
from rich.prompt   import Prompt, IntPrompt
from rich.progress import (Progress, TaskID, TextColumn,
                           BarColumn, TimeElapsedColumn)

# =============================================================================
# HASHCAT ENGINE
# =============================================================================

def find_hashcat_binary() -> str:
    """
    Dynamically locate the hashcat executable.
    Prioritizes:
      1. HASHCAT_PATH environment variable
      2. User's exact local pentesting location
      3. File next to the running script
      4. System PATH resolution
    """
    import shutil

    # 1. Environment Variable
    env_path = os.environ.get("HASHCAT_PATH")
    if env_path:
        return env_path

    # 2. Known Windows absolute installation path
    fixed_path = r"C:\Users\soyko\Documents\hassassini\hashcat-7.1.2\hashcat.exe"
    if os.path.exists(fixed_path):
        return fixed_path

    # 3. Next to script or child folder
    script_dir = Path(__file__).resolve().parent
    for name in ("hashcat.exe", "hashcat.bin", "hashcat"):
        # check parent/sibling folder hashcat/
        for p in (script_dir / name, script_dir / "hashcat" / name):
            if p.exists():
                return str(p)

    # 4. System PATH
    for name in ("hashcat.exe", "hashcat", "hashcat.bin"):
        p = shutil.which(name)
        if p:
            return p

    # Fallback to default
    return "hashcat.exe" if sys.platform == "win32" else "hashcat"


class HashcatEngine:
    """Wrapper for hashcat binary integration."""

    def __init__(self, hashcat_path: Optional[str] = None):
        self.hashcat_path = hashcat_path or find_hashcat_binary()
        self.session_name = "hash_cracker_v7"
        self.output_file  = str(Path("cracked.txt").resolve())
        self._check_hashcat()

    def _check_hashcat(self):
        try:
            hashcat_dir = str(Path(self.hashcat_path).resolve().parent)
            result = subprocess.run(
                [self.hashcat_path, "--version"],
                capture_output=True, text=True, errors="replace", timeout=5,
                cwd=hashcat_dir
            )
            self.version  = result.stdout.strip()
            self.available = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.available = False
            raise RuntimeError(
                f"hashcat no encontrado en '{self.hashcat_path}'. Instálalo o pasa la ruta con --hashcat-path"
            )

    # [F1] acepta salt; escribe «hash:salt» cuando corresponde
    def _build_hash_file(self, target_hash: bytes, salt: bytes = b"") -> str:
        """Escribe el fichero de hashes en el formato correcto para el modo usado."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hashes", delete=False) as f:
            line = target_hash.hex()
            if salt:
                line += ":" + salt.decode(errors="replace")
            f.write(line + "\n")
            return f.name

    # [F2] selector de modo centralizado
    def _hashcat_mode(self, salt: bytes) -> str:
        """1410 = sha256($pass.$salt)  |  1400 = sha256($pass)"""
        return "1410" if salt else "1400"

    # [F3] sin salt en la máscara; devuelve (mask_str, custom_args)
    def _build_mask(self, mask: str,
                    known_prefix: bytes = b"",
                    known_suffix: bytes = b"") -> Tuple[str, List[str]]:
        """
        Convierte la máscara interna a formato hashcat usando charsets personalizados
        (-1…-4) para que hashcat busque exactamente el mismo espacio que el motor Python.

        Devuelve (hc_mask, custom_args) donde custom_args son los flags
        ['-1', 'charset1', '-2', 'charset2', ...].
        """
        slot_map:       Dict[str, int] = {}
        custom_charsets: List[str]     = []

        hc_mask     = ""
        prefix_lit  = known_prefix.decode(errors="replace") if known_prefix else ""
        suffix_lit  = known_suffix.decode(errors="replace") if known_suffix else ""

        if prefix_lit:
            hc_mask += prefix_lit

        for c in mask:
            cs = CHARSETS[c]
            if cs not in slot_map:
                if len(custom_charsets) >= 4:
                    raise ValueError(
                        f"La máscara requiere más de 4 charsets únicos; "
                        f"hashcat sólo soporta 4 slots personalizados (-1 a -4). "
                        f"Simplifica la máscara."
                    )
                slot_map[cs] = len(custom_charsets) + 1
                custom_charsets.append(cs)
            hc_mask += f"?{slot_map[cs]}"

        if suffix_lit:
            hc_mask += suffix_lit

        custom_args: List[str] = []
        for i, cs in enumerate(custom_charsets, 1):
            custom_args.extend([f"-{i}", cs])

        return hc_mask, custom_args

    def _maybe_checkpoint(self, checkpoint_file: Optional[str],
                          pass_num: int, current_idx: int) -> None:
        if checkpoint_file:
            save_checkpoint(checkpoint_file, pass_num, current_idx)

    # [F4] modo dinámico, pasa salt, desempaqueta tuple
    def crack_mask(self, target_hash: bytes, mask: str,
                   known_prefix: bytes = b"", known_suffix: bytes = b"",
                   salt: bytes = b"", start_idx: int = 0,
                   checkpoint_file: Optional[str] = None) -> Optional[str]:
        """Ataque de máscara (hashcat -a 3). Devuelve la contraseña o None."""
        if not self.available:
            return None

        hash_file           = self._build_hash_file(target_hash, salt)
        hc_mask, custom_args = self._build_mask(mask, known_prefix, known_suffix)
        mode                 = self._hashcat_mode(salt)

        cmd = [
            self.hashcat_path,
            "-m", mode,
            "-a", "3",
            *custom_args,
            "-o", self.output_file,
            "--potfile-disable",
            "--force",
            "--status",
            "--status-timer", "10",
            "-O",
            "-w", "4",
            hash_file,
            hc_mask,
        ]

        if start_idx > 0:
            cmd.extend(["-s", str(start_idx)])
        cmd.extend(["--session", self.session_name])

        try:
            hashcat_dir = str(Path(self.hashcat_path).resolve().parent)
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, errors="replace",
                cwd=hashcat_dir
            )

            current_idx = start_idx
            if process.stdout is not None:
                for line in process.stdout:
                    if "Progress" in line:
                        m = re.search(r"Progress.*?:\s+(\d+)/\d+", line)
                        if m:
                            current_idx = int(m.group(1))
                            if checkpoint_file:
                                save_checkpoint(checkpoint_file, 1, current_idx)
                    if "STATUS" in line and "CRACKED" in line:
                        break

            process.wait()

            if Path(self.output_file).exists():
                with open(self.output_file) as f:
                    line = f.readline().strip()
                    if line:
                        # modo 1400: «hash:pass» | modo 1410: «hash:salt:pass»
                        return line.split(":", 2)[-1]
            return None

        finally:
            Path(hash_file).unlink(missing_ok=True)
            Path(self.output_file).unlink(missing_ok=True)

    # [F5] firma añade salt, modo dinámico, split corregido
    def crack_wordlist(self, target_hash: bytes, wordlist: str,
                       salt: bytes = b"",
                       rules: Optional[List[str]] = None) -> Optional[str]:
        """Ataque de diccionario (hashcat -a 0). Devuelve la contraseña o None."""
        if not self.available or not Path(wordlist).exists():
            return None

        hash_file = self._build_hash_file(target_hash, salt)
        mode      = self._hashcat_mode(salt)
        wordlist_abs = str(Path(wordlist).resolve())

        cmd = [
            self.hashcat_path,
            "-m", mode,
            "-a", "0",
            "-o", self.output_file,
            "--potfile-disable",
            "--force",
            "-O",
            "-w", "4",
            hash_file,
            wordlist_abs,
        ]

        if rules:
            for rule in rules:
                rule_abs = str(Path(rule).resolve()) if os.path.exists(rule) else rule
                cmd.extend(["-r", rule_abs])

        try:
            hashcat_dir = str(Path(self.hashcat_path).resolve().parent)
            subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600, cwd=hashcat_dir)

            if Path(self.output_file).exists():
                with open(self.output_file) as f:
                    line = f.readline().strip()
                    if line:
                        return line.split(":", 2)[-1]
            return None

        finally:
            Path(hash_file).unlink(missing_ok=True)
            Path(self.output_file).unlink(missing_ok=True)

    def benchmark(self) -> int:
        """Throughput hashcat para SHA-256 (H/s)."""
        try:
            hashcat_dir = str(Path(self.hashcat_path).resolve().parent)
            result = subprocess.run(
                [self.hashcat_path, "-b", "-m", "1400", "--force"],
                capture_output=True, text=True, errors="replace", timeout=60,
                cwd=hashcat_dir
            )
            m = re.search(r"Speed\.Dev\.\S+:\s+([\d.]+)\s+([GMk])H/s", result.stdout)
            if m:
                value, unit = float(m.group(1)), m.group(2)
                return int(value * {"k": 1e3, "M": 1e6, "G": 1e9}.get(unit, 1))
        except Exception:
            pass
        return 0


# =============================================================================
# SHA-256 CUDA KERNEL
# =============================================================================

SHA256_CUDA_KERNEL = r'''
extern "C" {

__constant__ unsigned int K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

__device__ __forceinline__ unsigned int rotr(unsigned int x, unsigned int n) {
    return (x >> n) | (x << (32 - n));
}

__device__ __forceinline__ void sha256_transform(unsigned int state[8],
                                                  const unsigned char block[64]) {
    unsigned int W[64];
    unsigned int a,b,c,d,e,f,g,h;
    for (int i = 0; i < 16; i++)
        W[i] = (block[i*4]<<24)|(block[i*4+1]<<16)|(block[i*4+2]<<8)|block[i*4+3];
    for (int i = 16; i < 64; i++) {
        unsigned int s0 = rotr(W[i-15],7)^rotr(W[i-15],18)^(W[i-15]>>3);
        unsigned int s1 = rotr(W[i-2],17)^rotr(W[i-2],19)^(W[i-2]>>10);
        W[i] = W[i-16]+s0+W[i-7]+s1;
    }
    a=state[0];b=state[1];c=state[2];d=state[3];
    e=state[4];f=state[5];g=state[6];h=state[7];
    for (int i = 0; i < 64; i++) {
        unsigned int S1   = rotr(e,6)^rotr(e,11)^rotr(e,25);
        unsigned int ch   = (e&f)^((~e)&g);
        unsigned int temp1= h+S1+ch+K[i]+W[i];
        unsigned int S0   = rotr(a,2)^rotr(a,13)^rotr(a,22);
        unsigned int maj  = (a&b)^(a&c)^(b&c);
        unsigned int temp2= S0+maj;
        h=g;g=f;f=e;e=d+temp1;d=c;c=b;b=a;a=temp1+temp2;
    }
    state[0]+=a;state[1]+=b;state[2]+=c;state[3]+=d;
    state[4]+=e;state[5]+=f;state[6]+=g;state[7]+=h;
}

__device__ void sha256_device(const unsigned char* input, unsigned int len,
                               unsigned char* output,
                               const unsigned char* prefix, unsigned int prefix_len,
                               const unsigned char* suffix, unsigned int suffix_len,
                               const unsigned char* salt,   unsigned int salt_len) {
    unsigned int state[8] = {
        0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
        0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19
    };
    unsigned char buffer[128];
    unsigned int total_len = prefix_len+len+suffix_len+salt_len;
    unsigned int bp = 0;
    for (unsigned int i = 0; i < prefix_len && bp < 128; i++) buffer[bp++] = prefix[i];
    for (unsigned int i = 0; i < len       && bp < 128; i++) buffer[bp++] = input[i];
    for (unsigned int i = 0; i < suffix_len&& bp < 128; i++) buffer[bp++] = suffix[i];
    for (unsigned int i = 0; i < salt_len  && bp < 128; i++) buffer[bp++] = salt[i];
    unsigned long long bit_len = (unsigned long long)total_len * 8;
    buffer[bp++] = 0x80;
    while ((bp % 64) != 56) { if (bp < 128) buffer[bp++] = 0; }
    for (int i = 7; i >= 0; i--) buffer[bp++] = (bit_len >> (i*8)) & 0xff;
    unsigned int num_blocks = (total_len + 8) / 64 + 1;
    for (unsigned int i = 0; i < num_blocks && i < 2; i++)
        sha256_transform(state, buffer + i*64);
    for (int i = 0; i < 8; i++) {
        output[i*4]   = (state[i]>>24)&0xff;
        output[i*4+1] = (state[i]>>16)&0xff;
        output[i*4+2] = (state[i]>> 8)&0xff;
        output[i*4+3] =  state[i]     &0xff;
    }
}

__global__ void sha256_brute_kernel(
        const unsigned int*  indices,
        const unsigned char* charset,
        const unsigned int*  charset_lens,
        const unsigned int   mask_len,
        const unsigned int   max_cs_len,
        const unsigned char* prefix,      const unsigned int prefix_len,
        const unsigned char* suffix,      const unsigned int suffix_len,
        const unsigned char* salt,        const unsigned int salt_len,
        const unsigned char* target,
        const unsigned int   prefix_bytes,
        unsigned int*        result_idx,
        unsigned char*       result_found,
        const unsigned int   batch_size) {

    unsigned int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid >= batch_size) return;

    unsigned int idx = indices[tid];
    unsigned char candidate[64];   /* aumentado de 16 → 64; máscaras más largas no corrompen memoria */
    unsigned char hash[32];

    unsigned int temp = idx;
    for (int i = (int)mask_len - 1; i >= 0; i--) {
        unsigned int cs_len = charset_lens[i];
        candidate[i] = charset[i * max_cs_len + (temp % cs_len)];
        temp /= cs_len;
    }

    sha256_device(candidate, mask_len, hash,
                  prefix, prefix_len, suffix, suffix_len, salt, salt_len);

    unsigned int match = 1;
    for (unsigned int i = 0; i < prefix_bytes && i < 32; i++)
        if (hash[i] != target[i]) { match = 0; break; }
    if (match && prefix_bytes < 32)
        for (unsigned int i = prefix_bytes; i < 32; i++)
            if (hash[i] != target[i]) { match = 0; break; }

    if (match) { *result_found = 1; *result_idx = tid; }
}
}
'''

# =============================================================================
# CHARSETS
# =============================================================================

CHARSETS: Dict[str, str] = {
    "N": "0123456789",
    "A": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "L": "abcdefghijklmnopqrstuvwxyz",
    "U": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    # "!" = punctuation slot (NOT literal "!" every time); includes common symbols + !
    "!": "!$%&\"/@#._-",
    "H": "0123456789abcdef",
    "?": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!$%&/",
}

DEFAULT_MASK = "NNNNNNAA!"

# Límite de longitud alineado con el buffer CUDA (candidate[64])
MASK_MAX_LEN      = 64
# hashcat solo tiene 4 slots de charset personalizados (-1 … -4)
MASK_MAX_CHARSETS = 4

def validate_mask(mask: str) -> None:
    """
    Valida la máscara antes de arrancar cualquier motor (CPU, GPU o hashcat).

    Raises ValueError si:
      · algún carácter no existe en CHARSETS
      · la longitud supera MASK_MAX_LEN  (buffer CUDA = 64)
      · se usan más de MASK_MAX_CHARSETS charsets distintos (límite hashcat)
    """
    bad = [c for c in mask if c not in CHARSETS]
    if bad:
        raise ValueError(
            f"Caracteres de máscara no reconocidos: {bad}. "
            f"Permitidos: {list(CHARSETS.keys())}"
        )
    if len(mask) > MASK_MAX_LEN:
        raise ValueError(
            f"Máscara demasiado larga ({len(mask)} chars). "
            f"Máximo permitido: {MASK_MAX_LEN} "
            f"(límite del buffer CUDA candidate[{MASK_MAX_LEN}])."
        )
    unique_cs = len({CHARSETS[c] for c in mask})
    if unique_cs > MASK_MAX_CHARSETS:
        raise ValueError(
            f"La máscara usa {unique_cs} charsets únicos, "
            f"pero hashcat solo soporta {MASK_MAX_CHARSETS} slots personalizados (-1…-4). "
            f"Reduce la variedad de tipos de caracteres en la máscara."
        )

# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class Config:
    target_hash:    bytes
    mask:           str
    salt:           bytes
    cpu_workers:    int
    cpu_chunk_size: int
    gpu_batch_size: int
    gpu_streams:    int
    prefix_bytes:   int
    interactive:    bool
    min_len:        int
    start_idx:      int
    end_idx:        Optional[int]
    known_prefix:   bytes
    known_suffix:   bytes
    wordlist:       Optional[str]
    checkpoint:     Optional[str]
    gpu_enabled:    bool  = True
    use_hashcat:    bool  = True
    cpu_gpu_ratio:  float = 0.3

# =============================================================================
# MASK ENGINE
# =============================================================================

def build_charsets(mask: str) -> List[str]:
    return [CHARSETS[c] for c in mask]

def total_space(charsets: List[str]) -> int:
    t = 1
    for cs in charsets:
        t *= len(cs)
    return t

def idx_to_candidate(idx: int, charsets: List[str]) -> bytes:
    out = []
    for cs in reversed(charsets):
        idx, rem = divmod(idx, len(cs))
        out.append(cs[rem])
    return "".join(reversed(out)).encode()

# =============================================================================
# CPU WORKERS  (funciones de módulo — deben ser picklables para ProcessPool)
# =============================================================================

def make_digest(candidate: bytes, known_prefix: bytes,
                known_suffix: bytes, salt: bytes) -> bytes:
    return hashlib.sha256(known_prefix + candidate + known_suffix + salt).digest()


def cpu_worker(args):
    """Worker de proceso para brute-force clásico."""
    (start, end, charsets, target_hash, prefix_bytes,
     known_prefix, known_suffix, salt, stop_flag) = args

    checked = 0
    for idx in range(start, end):
        if stop_flag.is_set():
            return None, checked, idx
        candidate = idx_to_candidate(idx, charsets)
        digest    = make_digest(candidate, known_prefix, known_suffix, salt)
        if digest[:prefix_bytes] == target_hash[:prefix_bytes] and digest == target_hash:
            stop_flag.set()
            full = (known_prefix + candidate + known_suffix).decode(errors="replace")
            return full, checked + 1, idx
        checked += 1
    return None, checked, end


def wordlist_worker(args):
    """Worker de proceso para ataque de diccionario."""
    (words, target_hash, prefix_bytes, known_prefix, known_suffix, salt) = args
    for word in words:
        candidate = word.encode()
        digest    = make_digest(candidate, known_prefix, known_suffix, salt)
        if digest[:prefix_bytes] == target_hash[:prefix_bytes] and digest == target_hash:
            full = (known_prefix + candidate + known_suffix).decode(errors="replace")
            return full, len(words)
    return None, len(words)

# =============================================================================
# GPU ENGINE
# =============================================================================

class GPUEngine:
    def __init__(self, config: Config):
        self.config    = config
        self.enabled   = False
        self.kernel    = None
        self.stream    = None
        self.name      = "N/A"
        self.sm_count  = 0
        self.total_mem = 0

        if not GPU_AVAILABLE or not config.gpu_enabled:
            return

        try:
            self.device = cp.cuda.Device(0)
            self.device.use()
            self.module  = cp.RawModule(code=SHA256_CUDA_KERNEL)
            self.kernel  = self.module.get_function("sha256_brute_kernel")
            self.stream  = cp.cuda.Stream(non_blocking=True)
            props        = cp.cuda.runtime.getDeviceProperties(0)
            self.name      = props["name"].decode()
            self.sm_count  = props["multiProcessorCount"]
            self.total_mem = props["totalGlobalMem"] // (1024 ** 2)
            self._warmup()
            self.enabled = True
        except Exception as e:
            self.enabled = False
            self._err    = str(e)

    def _warmup(self):
        cp.zeros(64, dtype=cp.uint8)
        if self.stream is not None:
            self.stream.synchronize()

    def benchmark(self) -> int:
        if not self.enabled:
            return 0
        try:
            batch = min(self.config.gpu_batch_size, 500_000)
            arr   = cp.zeros((batch, 32), dtype=cp.uint8)
            t0    = time.time()
            for _ in range(20):
                cp.bitwise_xor(arr, 0xAA, out=arr)
            cp.cuda.Stream.null.synchronize()
            return int(batch * 20 / max(time.time() - t0, 1e-9))
        except Exception:
            self.enabled = False
            return 0

    def stats(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        try:
            free, total = cp.cuda.runtime.memGetInfo()
            return {
                "enabled":      True,
                "name":         self.name,
                "sm_count":     self.sm_count,
                "mem_free_mb":  free  // (1024 ** 2),
                "mem_total_mb": total // (1024 ** 2),
            }
        except Exception:
            return {"enabled": False}

    def crack_batch(self, indices: np.ndarray, charsets: List[str],
                    target_hash: bytes) -> Tuple[Optional[int], int]:
        if not self.enabled:
            return None, 0

        batch_size   = len(indices)
        max_cs_len   = max(len(cs) for cs in charsets)
        charset_arr  = np.zeros((len(charsets), max_cs_len), dtype=np.uint8)
        charset_lens = np.zeros(len(charsets), dtype=np.uint32)
        for i, cs in enumerate(charsets):
            charset_arr[i, :len(cs)] = np.frombuffer(cs.encode(), dtype=np.uint8)
            charset_lens[i] = len(cs)

        d_indices      = cp.asarray(indices,      dtype=cp.uint32)
        d_charset      = cp.asarray(charset_arr,  dtype=cp.uint8)
        d_charset_lens = cp.asarray(charset_lens, dtype=cp.uint32)
        d_prefix       = cp.asarray(np.frombuffer(self.config.known_prefix, dtype=np.uint8))
        d_suffix       = cp.asarray(np.frombuffer(self.config.known_suffix, dtype=np.uint8))
        d_salt         = cp.asarray(np.frombuffer(self.config.salt,         dtype=np.uint8))
        d_target       = cp.asarray(np.frombuffer(target_hash,              dtype=np.uint8))
        d_result_idx   = cp.zeros(1, dtype=cp.uint32)
        d_result_found = cp.zeros(1, dtype=cp.uint8)
        
        if self.kernel is None or self.stream is None:
            return None, batch_size

        threads = 256
        blocks  = (batch_size + threads - 1) // threads
        self.kernel(
            (blocks,), (threads,),
            (d_indices, d_charset, d_charset_lens, np.uint32(len(charsets)),
             np.uint32(max_cs_len),
             d_prefix, np.uint32(len(self.config.known_prefix)),
             d_suffix, np.uint32(len(self.config.known_suffix)),
             d_salt,   np.uint32(len(self.config.salt)),
             d_target, np.uint32(self.config.prefix_bytes),
             d_result_idx, d_result_found, np.uint32(batch_size))
        )
        self.stream.synchronize()

        if int(d_result_found.get()):
            return int(d_result_idx.get()), batch_size
        return None, batch_size

# =============================================================================
# INTERACTIVE CONTROLLER
# =============================================================================

class InteractiveController:
    HELP = (
        "[dim]Comandos en vivo:[/dim]\n"
        "  cpu=N       → cambiar workers\n"
        "  chunk=N     → cambiar chunk size\n"
        "  gpu=N       → cambiar GPU batch size\n"
        "  prefix=N    → cambiar prefix bytes\n"
        "  status      → mostrar config actual\n"
        "  help / ?    → esta ayuda"
    )

    def __init__(self, config: Config, console: Console):
        self.config  = config
        self.console = console
        self._q      = queue.SimpleQueue()
        self._t      = threading.Thread(target=self._reader, daemon=True)

    def start(self):
        self._t.start()

    def _reader(self):
        while True:
            try:
                self._q.put(input())
            except EOFError:
                break

    def poll(self):
        while not self._q.empty():
            self._apply(self._q.get_nowait().strip())

    def _apply(self, cmd: str):
        cmd_l = cmd.lower()
        try:
            if cmd_l in ("help", "?", "h"):
                self.console.log(self.HELP)
            elif cmd_l == "status":
                self.console.log(
                    f"[cyan]cpu={self.config.cpu_workers}  "
                    f"chunk={self.config.cpu_chunk_size:,}  "
                    f"gpu_batch={self.config.gpu_batch_size:,}  "
                    f"prefix={self.config.prefix_bytes}[/cyan]"
                )
            elif "=" in cmd_l:
                key, val = cmd_l.split("=", 1)
                n = int(val)
                mapping = {
                    "cpu":    "cpu_workers",
                    "chunk":  "cpu_chunk_size",
                    "gpu":    "gpu_batch_size",
                    "prefix": "prefix_bytes",
                }
                attr = mapping.get(key)
                if attr:
                    setattr(self.config, attr, n)
                    self.console.log(f"[cyan]{attr} → {n:,}[/cyan]")
                else:
                    self.console.log(
                        f"[red]Clave desconocida '{key}'. Escribe 'help'.[/red]"
                    )
            else:
                self.console.log("[red]Comando no reconocido. Escribe 'help'.[/red]")
        except (ValueError, IndexError):
            self.console.log("[red]Valor inválido.[/red]")

# =============================================================================
# CHECKPOINT
# =============================================================================

def save_checkpoint(path: str, pass_num: int, idx: int) -> None:
    with open(path, "w") as f:
        json.dump({"pass": pass_num, "idx": idx}, f)

def load_checkpoint(path: str) -> Tuple[int, int]:
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("pass", 1), d.get("idx", 0)
    except Exception:
        return 1, 0

# =============================================================================
# INTERACTIVE WIZARD
# =============================================================================

def prompt_config(console: Console, args) -> Config:
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Hash Cracker v7 — Configuración Interactiva[/bold cyan]\n"
        "Presiona [bold]Enter[/bold] para aceptar el valor por defecto.",
        title="Setup"
    ))
    console.print()

    while True:
        raw = Prompt.ask(
            "[bold yellow]Target SHA-256[/bold yellow] (hex 64 chars)",
            default=getattr(args, "target", "") or ""
        ).strip()
        try:
            tb = bytes.fromhex(raw)
            assert len(tb) == 32
            break
        except Exception:
            console.print(
                "[red]Hash inválido — debe ser hex de exactamente 64 caracteres.[/red]"
            )

    console.print("\n[dim]sha256(prefijo_fijo + candidato + sufijo_fijo + salt)[/dim]")
    salt_raw   = Prompt.ask(
        "[bold yellow]Salt[/bold yellow] (vacío si no aplica)",
        default=getattr(args, "salt", "") or ""
    )
    salt_bytes = salt_raw.encode() if salt_raw else b""

    kp = Prompt.ask(
        "[bold yellow]Prefijo conocido[/bold yellow] (parte fija al INICIO)",
        default=getattr(args, "known_prefix", "") or ""
    )
    ks = Prompt.ask(
        "[bold yellow]Sufijo conocido[/bold yellow]  (parte fija al FINAL, antes del salt)",
        default=getattr(args, "known_suffix", "") or ""
    )

    valid = list(CHARSETS.keys())
    console.print(
        "\n[dim]Tipos: N=dígitos  A=alfanum  L=minúsc  U=mayúsc  !=símbolos  H=hex  ?=todos[/dim]"
    )
    while True:
        mask = Prompt.ask(
            "[bold yellow]Máscara[/bold yellow]",
            default=getattr(args, "mask", DEFAULT_MASK) or DEFAULT_MASK
        ).strip()
        try:
            validate_mask(mask)
            break
        except ValueError as e:
            console.print(f"[red]{e}[/red]")

    console.print(
        f"\n[dim]min_len < {len(mask)} activa modo incremental (longitudes min_len…{len(mask)})[/dim]"
    )
    min_len = IntPrompt.ask(
        f"[bold yellow]Longitud mínima[/bold yellow] (1–{len(mask)})", default=len(mask)
    )
    min_len = max(1, min(len(mask), min_len))

    wl = Prompt.ask(
        "[bold yellow]Wordlist[/bold yellow] (ruta a fichero, vacío para omitir)",
        default=getattr(args, "wordlist", "") or ""
    ).strip()
    wordlist = wl if wl and Path(wl).is_file() else None
    if wl and not wordlist:
        console.print("[yellow]Fichero no encontrado, se omitirá wordlist.[/yellow]")

    default_cpu = max(1, mp.cpu_count() - 2)
    cpu_workers  = IntPrompt.ask("[bold yellow]CPU workers[/bold yellow]",    default=default_cpu)
    chunk        = IntPrompt.ask("[bold yellow]Chunk size[/bold yellow]",     default=2_000_000)
    gpu_batch    = IntPrompt.ask("[bold yellow]GPU batch size[/bold yellow]", default=1_000_000)
    prefix       = IntPrompt.ask("[bold yellow]Prefix bytes[/bold yellow]",   default=4)
    prefix       = max(1, min(32, prefix))

    console.print("\n[dim]Para reanudar, indica el índice donde quedaste.[/dim]")
    start_idx   = IntPrompt.ask(
        "[bold yellow]Iniciar en índice[/bold yellow] (0 = inicio)", default=0
    )
    end_idx_raw = Prompt.ask(
        "[bold yellow]Terminar en índice[/bold yellow] (vacío = hasta el final)", default=""
    )
    end_idx = int(end_idx_raw) if end_idx_raw.strip().isdigit() else None

    ckpt = Prompt.ask(
        "[bold yellow]Fichero checkpoint[/bold yellow] (vacío para no guardar progreso)",
        default=getattr(args, "checkpoint", "") or ""
    ).strip()

    use_gpu = Prompt.ask(
        "[bold yellow]¿Usar GPU?[/bold yellow]", choices=["s", "n"], default="s"
    ) == "s"

    console.print()
    return Config(
        target_hash    = tb,
        mask           = mask,
        salt           = salt_bytes,
        cpu_workers    = cpu_workers,
        cpu_chunk_size = chunk,
        gpu_batch_size = gpu_batch,
        gpu_streams    = 2,
        prefix_bytes   = prefix,
        interactive    = True,
        min_len        = min_len,
        start_idx      = start_idx,
        end_idx        = end_idx,
        known_prefix   = kp.encode() if kp else b"",
        known_suffix   = ks.encode() if ks else b"",
        wordlist       = wordlist,
        checkpoint     = ckpt or None,
        gpu_enabled    = use_gpu,
    )

# =============================================================================
# HYBRID SEARCHER
# =============================================================================

class HybridSearcher:
    CKPT_INTERVAL = 10.0

    def __init__(self, config: Config):
        self.config       = config
        self.console      = Console()
        self.stop_event   = threading.Event()
        self.found_result: Optional[str] = None

        # Validar la máscara antes de inicializar cualquier motor
        validate_mask(config.mask)

        self.hashcat:       Optional[HashcatEngine] = None
        self.gpu:           Optional[GPUEngine]     = None
        self.using_hashcat  = False
        self.resume_pass    = 1          # [F10] siempre inicializado

        use_hashcat = config.gpu_enabled and getattr(config, "use_hashcat", True)
        if use_hashcat:
            try:
                hc_path      = find_hashcat_binary()
                self.hashcat = HashcatEngine(hc_path)
                self.using_hashcat = True
                self.console.print(
                    f"[green]✓ Hashcat listo:[/green] {self.hashcat.version}"
                )
            except RuntimeError as e:
                self.console.print(
                    f"[yellow]Hashcat no disponible ({e}), usando CuPy/CPU[/yellow]"
                )
                self.gpu = GPUEngine(config)
        elif config.gpu_enabled:
            self.console.print("[cyan]Hashcat desactivado — modo CuPy/CPU[/cyan]")
            self.gpu = GPUEngine(config)

        self.cpu_checked = 0
        self.gpu_checked = 0
        self._last_ckpt  = time.time()
        self.next_idx    = config.start_idx
        self._idx_lock   = threading.Lock()

        if config.interactive:
            self.controller = InteractiveController(config, self.console)

    # ── checkpoint ───────────────────────────────────────────────────────────

    def _maybe_checkpoint(self, pass_num: int, idx: int) -> None:
        """Guarda checkpoint sólo si ha pasado CKPT_INTERVAL desde el último."""
        if not self.config.checkpoint:
            return
        now = time.time()
        if now - self._last_ckpt >= self.CKPT_INTERVAL:
            save_checkpoint(self.config.checkpoint, pass_num, idx)
            self._last_ckpt = now

    # ── work distribution ────────────────────────────────────────────────────

    def _get_chunk(self, size: int, space: int) -> Optional[Tuple[int, int]]:
        with self._idx_lock:
            if self.stop_event.is_set():
                return None
            end_limit = self.config.end_idx or space
            start     = self.next_idx
            end       = min(start + size, end_limit)
            if end <= start:
                return None
            self.next_idx = end
            return start, end

    # ── CPU thread ───────────────────────────────────────────────────────────

    def _cpu_thread(self, charsets: List[str], target_hash: bytes, space: int) -> None:
        cfg = self.config
        while not self.stop_event.is_set():
            chunk = self._get_chunk(cfg.cpu_chunk_size, space)
            if chunk is None:
                break
            start, end = chunk
            result, checked, _ = cpu_worker((
                start, end, charsets, target_hash,
                cfg.prefix_bytes, cfg.known_prefix, cfg.known_suffix,
                cfg.salt, self.stop_event,
            ))
            self.cpu_checked += checked  # seguro bajo GIL
            if result:
                self.found_result = result
                self.stop_event.set()
                break

    # ── GPU thread ───────────────────────────────────────────────────────────

    def _gpu_thread(self, charsets: List[str], target_hash: bytes, space: int) -> None:
        cfg = self.config
        if self.gpu is None:
            return
        while not self.stop_event.is_set():
            chunk = self._get_chunk(cfg.gpu_batch_size, space)
            if chunk is None:
                break
            start, end  = chunk
            indices     = np.arange(start, end, dtype=np.uint32)
            match_pos, processed = self.gpu.crack_batch(indices, charsets, target_hash)
            self.gpu_checked += processed
            if match_pos is not None:
                global_idx = int(indices[match_pos])
                candidate  = idx_to_candidate(global_idx, charsets)
                full = (
                    cfg.known_prefix + candidate + cfg.known_suffix
                ).decode(errors="replace")
                self.found_result = full
                self.stop_event.set()
                break

    # ── wordlist ─────────────────────────────────────────────────────────────

    def _run_wordlist(self, start_time: float) -> Optional[str]:
        path = self.config.wordlist
        if not path:
            return None

        if self.hashcat:
            self.console.print(
                f"\n[bold]→ Wordlist vía hashcat:[/bold] [cyan]{path}[/cyan]"
            )
            # [F7] pasa salt
            result = self.hashcat.crack_wordlist(
                self.config.target_hash, path, salt=self.config.salt
            )
            if result:
                elapsed = time.time() - start_time
                self.console.print(
                    f"\n[bold green]✓ ENCONTRADO[/bold green] → "
                    f"[bold]{result}[/bold] ({elapsed:.2f}s)"
                )
                return result
            return None

        return self._run_wordlist_cpu(start_time)

    # [F8] implementado
    def _run_wordlist_cpu(self, start_time: float) -> Optional[str]:
        """Fallback wordlist attack usando ProcessPoolExecutor."""
        path = self.config.wordlist
        if not path or not Path(path).is_file():
            return None

        cfg = self.config
        self.console.print(
            f"\n[bold]→ Wordlist CPU:[/bold] [cyan]{path}[/cyan]"
        )

        try:
            with open(path, "r", errors="ignore") as f:
                all_words = [ln.strip() for ln in f if ln.strip()]
        except Exception as e:
            self.console.print(f"[red]Error leyendo wordlist: {e}[/red]")
            return None

        if not all_words:
            return None

        chunk_size = max(500, len(all_words) // max(cfg.cpu_workers * 8, 1))
        chunks     = [
            all_words[i: i + chunk_size]
            for i in range(0, len(all_words), chunk_size)
        ]

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                "[green]Wordlist CPU[/green]", total=len(all_words)
            )

            with ProcessPoolExecutor(max_workers=cfg.cpu_workers) as executor:
                futures = {
                    executor.submit(wordlist_worker, (
                        chunk, cfg.target_hash, cfg.prefix_bytes,
                        cfg.known_prefix, cfg.known_suffix, cfg.salt,
                    )): chunk
                    for chunk in chunks
                }

                for future in as_completed(futures):
                    try:
                        result, count = future.result()
                    except Exception:
                        continue

                    progress.update(task, advance=count)

                    if result:
                        for pending in futures:
                            pending.cancel()
                        elapsed = time.time() - start_time
                        self.console.print(
                            f"\n[bold green]✓ ENCONTRADO[/bold green] → "
                            f"[bold]{result}[/bold] ({elapsed:.2f}s)"
                        )
                        return result

        return None

    # ── hashcat progress wrapper ──────────────────────────────────────────────

    # [F6] modo dinámico, pasa salt, total=None → se fija al parsear Progress
    def _run_hashcat_with_progress(self, mask: str, space: int,
                                   pass_num: int, start_time: float) -> Optional[str]:
        cfg = self.config
        if self.hashcat is None:
            return None

        hash_file            = self.hashcat._build_hash_file(cfg.target_hash, cfg.salt)
        hc_mask, custom_args = self.hashcat._build_mask(mask, cfg.known_prefix, cfg.known_suffix)
        mode                 = self.hashcat._hashcat_mode(cfg.salt)
        skip                 = self.next_idx if pass_num == self.resume_pass else 0
        hashcat_dir          = str(Path(self.hashcat.hashcat_path).resolve().parent)

        cmd = [
            self.hashcat.hashcat_path,
            "-m", mode,
            "-a", "3",
            *custom_args,
            "-o", self.hashcat.output_file,
            "--potfile-disable",
            "--force",
            "--status",
            "--status-timer", "1",
            "-O", "-w", "4",
        ]

        if skip > 0:
            cmd.extend(["-s", str(skip)])
        if cfg.end_idx:
            cmd.extend(["-l", str(cfg.end_idx)])

        cmd.extend(["--session", f"hc_v7_pass{pass_num}", hash_file, hc_mask])

        self._last_hashcat_lines: list[str] = []

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TextColumn("[cyan]{task.fields[rate]}[/cyan]"),
            console=self.console,
        ) as progress:

            # total=None → Rich muestra spinner hasta que lo fijemos
            task = progress.add_task(
                f"[green]Hashcat Pass {pass_num} [{mask}][/green]",
                total=None,
                rate="calculando...",
            )

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                errors="replace",
                cwd=hashcat_dir
            )

            hashcat_total: Optional[int] = None
            last_progress = skip
            last_update   = time.time()

            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        line = line.strip()
                        self._last_hashcat_lines.append(line)
                        if len(self._last_hashcat_lines) > 40:
                            self._last_hashcat_lines.pop(0)

                        # Parsear progreso real de hashcat
                        # Formato: Progress.........: 123456/297825000000 (0.04%)
                        if "Progress" in line and "/" in line:
                            m = re.search(r"Progress.*?:\s+(\d+)/(\d+)", line)
                            if m:
                                current  = int(m.group(1))
                                total_hc = int(m.group(2))

                                # Fijar el total real la primera vez que lo recibimos
                                if hashcat_total is None:
                                    hashcat_total = total_hc
                                    progress.update(task, total=max(1, hashcat_total - skip))

                                now     = time.time()
                                elapsed = now - last_update
                                if elapsed >= 1.0:
                                    delta = current - last_progress
                                    rate  = delta / max(elapsed, 1e-9)
                                    progress.update(
                                        task,
                                        completed=current - skip,
                                        rate=f"{rate:,.0f} H/s",
                                    )
                                    last_update   = now
                                    last_progress = current
                                    self.hashcat._maybe_checkpoint(
                                        cfg.checkpoint, pass_num, current
                                    )
                                    if cfg.interactive:
                                        self.controller.poll()

                        if "STATUS" in line and "CRACKED" in line:
                            break

                        if self.stop_event.is_set():
                            process.terminate()
                            break

            finally:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                except Exception:
                    pass
                Path(hash_file).unlink(missing_ok=True)

        output = Path(self.hashcat.output_file)
        if output.exists():
            with open(output) as f:
                line = f.readline().strip()
            output.unlink(missing_ok=True)
            if line:
                return line.split(":", 2)[-1]

        for hint in getattr(self, "_last_hashcat_lines", [])[-8:]:
            if any(k in hint for k in ("Status", "Progress", "ERROR", "Token", "Rejected")):
                self.console.print(f"[dim]{hint}[/dim]")
        return None

    # ── pass dispatcher ───────────────────────────────────────────────────────

    def _run_pass(self, submask: str, charsets: List[str], space: int,
                  pass_num: int, total_passes: int, start_time: float) -> Optional[str]:

        # ── Hashcat path ──────────────────────────────────────────────────────
        if self.using_hashcat and self.hashcat:
            result = self._run_hashcat_with_progress(submask, space, pass_num, start_time)
            if result:
                elapsed = time.time() - start_time
                self.console.print(
                    f"\n[bold green]✓ ENCONTRADO[/bold green] → [bold]{result}[/bold]"
                )
                self.console.print(f"Tiempo: {elapsed:.2f}s | Hashcat GPU")
                return result
            return None

        # ── CuPy / CPU fallback ───────────────────────────────────────────────
        self.cpu_checked = 0
        self.gpu_checked = 0
        self.stop_event.clear()

        # [F9a] base correcta para la barra: espacio restante desde next_idx
        task_total = max(1, space - self.next_idx)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TextColumn("[cyan]{task.fields[rate]:,.0f} H/s[/cyan]"),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                f"[green]Pass {pass_num}/{total_passes} [{submask}][/green]",
                total=task_total,
                rate=0.0,
            )

            threads: List[threading.Thread] = []

            for _ in range(self.config.cpu_workers):
                t = threading.Thread(
                    target=self._cpu_thread,
                    args=(charsets, self.config.target_hash, space),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            # [F9b] guarda contra self.gpu siendo None
            if self.gpu and self.gpu.enabled:
                gt = threading.Thread(
                    target=self._gpu_thread,
                    args=(charsets, self.config.target_hash, space),
                    daemon=True,
                )
                gt.start()
                threads.append(gt)

            last_update  = time.time()
            last_checked = 0

            while any(t.is_alive() for t in threads):
                time.sleep(0.1)
                now     = time.time()
                elapsed = now - last_update
                if elapsed >= 0.5:
                    total_c = self.cpu_checked + self.gpu_checked
                    rate    = (total_c - last_checked) / max(elapsed, 1e-9)
                    progress.update(task, completed=total_c, rate=rate)
                    last_update  = now
                    last_checked = total_c

                # [F9c] checkpoint del HybridSearcher, no del HashcatEngine
                self._maybe_checkpoint(pass_num, self.next_idx)

                if self.config.interactive:
                    self.controller.poll()

            progress.update(
                task,
                completed=self.cpu_checked + self.gpu_checked,
                rate=0.0,
            )

        for t in threads:
            t.join(timeout=1)

        if self.found_result:
            elapsed = time.time() - start_time
            total_c = self.cpu_checked + self.gpu_checked
            self.console.print(
                f"\n[bold green]✓ ENCONTRADO[/bold green] → [bold]{self.found_result}[/bold]"
            )
            self.console.print(
                f"Tiempo: {elapsed:.2f}s | CPU: {self.cpu_checked:,} | "
                f"GPU: {self.gpu_checked:,} | Total: {total_c:,}"
            )
            return self.found_result
        return None

    # ── main entry point ─────────────────────────────────────────────────────

    def run(self) -> Optional[str]:
        console = self.console
        console.print()

        if self.using_hashcat and self.hashcat is not None:
            speed = self.hashcat.benchmark()
            console.print(Panel.fit(
                f"[green]Hashcat GPU:[/green] {self.hashcat.version}\n"
                f"Benchmark SHA-256: ~{speed:,} H/s\n"
                f"[dim]Modo 1410 (sha256(pass+salt)) cuando hay salt[/dim]",
                title="Engine"
            ))
        elif self.gpu and self.gpu.enabled:
            bench = self.gpu.benchmark()
            st    = self.gpu.stats()
            console.print(Panel.fit(
                f"[green]CuPy GPU:[/green] {self.gpu.name}\n"
                f"SMs: {self.gpu.sm_count} | "
                f"Mem: {st['mem_free_mb']}/{st['mem_total_mb']} MB\n"
                f"Throughput: ~{bench:,} ops/s",
                title="CUDA"
            ))
        else:
            console.print("[yellow]Modo CPU-only[/yellow]")

        if self.config.interactive:
            console.print("[dim]Comandos: cpu=N chunk=N prefix=N status help[/dim]\n")
            self.controller.start()

        # Resume desde checkpoint
        resume_pass, resume_idx = 1, self.config.start_idx
        if self.config.checkpoint and Path(self.config.checkpoint).exists():
            resume_pass, resume_idx = load_checkpoint(self.config.checkpoint)
            self.next_idx    = resume_idx
            self.resume_pass = resume_pass
            console.print(
                f"[cyan]Reanudando: pass {resume_pass}, idx {resume_idx:,}[/cyan]"
            )

        start_time = time.time()

        # Fase 1: Wordlist
        result = self._run_wordlist(start_time)
        if result:
            return result

        # Fase 2: Brute-force incremental
        passes = [
            (self.config.mask[:l],
             build_charsets(self.config.mask[:l]),
             total_space(build_charsets(self.config.mask[:l])))
            for l in range(self.config.min_len, len(self.config.mask) + 1)
        ]

        for i, (submask, charsets, space) in enumerate(passes, 1):
            if i < resume_pass:
                console.print(f"[dim]Saltando pass {i} (checkpoint)[/dim]")
                continue

            console.print(
                f"\n[bold]→ Pass {i}/{len(passes)}[/bold]: "
                f"máscara=[cyan]{submask}[/cyan]  "
                f"espacio=[yellow]{space:,}[/yellow]"
            )
            if space > 5_000_000_000_000 and not self.config.end_idx:
                console.print(
                    "[yellow]⚠ Keyspace > 5T — use 'Terminar en índice' / --end-idx for slices, "
                    "or confirm mask ('!' = punctuation charset, not literal '!!').[/yellow]"
                )

            if i != resume_pass:
                self.next_idx = 0

            result = self._run_pass(submask, charsets, space, i, len(passes), start_time)
            if result:
                return result
            if self.stop_event.is_set():
                break

        console.print("\n[red]✗ No encontrado.[/red]")
        return None

# =============================================================================
# MAIN
# =============================================================================

def main():
    p = argparse.ArgumentParser(description="Hybrid Hash Cracker v7 + Hashcat")
    p.add_argument("--hashcat-path",  default=os.environ.get("HASHCAT_PATH", "hashcat.exe"),
                   help="Ruta al binario hashcat")
    p.add_argument("--use-hashcat",   action="store_true",
                   help="Forzar uso de hashcat (falla si no está disponible)")
    p.add_argument("--no-hashcat",    action="store_true",
                   help="Desactivar hashcat; usar CuPy/CPU")
    p.add_argument("--target",        default=None,  help="SHA-256 hex (64 chars)")
    p.add_argument("--salt",          default=None,  help="Salt concatenado al candidato")
    p.add_argument("--mask",          default=None,  help="Máscara (N A L U ! H ?)")
    p.add_argument("--min-len",       type=int, default=None)
    p.add_argument("--known-prefix",  default=None)
    p.add_argument("--known-suffix",  default=None)
    p.add_argument("--wordlist",      default=None)
    p.add_argument("--checkpoint",    default=None)
    p.add_argument("--cpu",           type=int, default=None)
    p.add_argument("--chunk",         type=int, default=None)
    p.add_argument("--gpu-batch",     type=int, default=None)
    p.add_argument("--prefix-bytes",  type=int, default=None)
    p.add_argument("--start-idx",     type=int, default=0)
    p.add_argument("--end-idx",       type=int, default=None)
    p.add_argument("--no-gpu",        action="store_true")
    p.add_argument("--interactive",   action="store_true")
    args = p.parse_args()

    if args.use_hashcat:
        os.environ["HASHCAT_PATH"] = args.hashcat_path

    console = Console()

    if args.interactive or args.target is None:
        config = prompt_config(console, args)
    else:
        mask = args.mask or DEFAULT_MASK
        config = Config(
            target_hash    = bytes.fromhex(args.target),
            mask           = mask,
            salt           = (args.salt or "").encode(),
            cpu_workers    = args.cpu          or max(1, mp.cpu_count() - 2),
            cpu_chunk_size = args.chunk        or 2_000_000,
            gpu_batch_size = args.gpu_batch    or 1_000_000,
            gpu_streams    = 2,
            prefix_bytes   = args.prefix_bytes or 4,
            interactive    = False,
            min_len        = args.min_len      or len(mask),
            start_idx      = args.start_idx,
            end_idx        = args.end_idx,
            known_prefix   = (args.known_prefix or "").encode(),
            known_suffix   = (args.known_suffix or "").encode(),
            wordlist       = args.wordlist,
            checkpoint     = args.checkpoint,
            gpu_enabled    = not args.no_gpu,
            use_hashcat    = not args.no_hashcat,
        )

    result = HybridSearcher(config).run()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
