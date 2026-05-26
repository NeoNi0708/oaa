# -*- coding: utf-8 -*-
"""OAA Local LLM 检测与启动管理"""

import os, sys, subprocess, json, time, logging

logger = logging.getLogger(__name__)

LLAMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli", "llama")
DEFAULT_MODEL_NAME = "BitCPM4-1B-q4_0.gguf"

def detect_gpu():
    """检测 NVIDIA GPU 及 CUDA 兼容性"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            name = parts[0] if len(parts) > 0 else "unknown"
            cc = float(parts[1]) if len(parts) > 1 and parts[1] else 0
            driver = parts[2] if len(parts) > 2 else "unknown"
            # Compute Capability >= 5.0 才能用 llama.cpp CUDA
            cuda_supported = cc >= 5.0
            return {
                "available": True,
                "name": name,
                "compute_capability": cc,
                "driver_version": driver,
                "cuda_supported": cuda_supported,
                "vram": _get_vram()
            }
    except Exception as e:
        logger.debug(f"GPU detection failed: {e}")
    return {"available": False, "name": "", "compute_capability": 0,
            "driver_version": "", "cuda_supported": False, "vram": 0}


def _get_vram():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().split()[0])
    except:
        pass
    return 0


def get_llama_server_path(gpu_info=None):
    """根据 GPU 情况选择合适的 llama-server"""
    if gpu_info is None:
        gpu_info = detect_gpu()

    cuda_exe = os.path.join(LLAMA_DIR, "llama-server.exe")
    cpu_exe = os.path.join(LLAMA_DIR, "llama-server.exe")
    cuda_dll = os.path.join(LLAMA_DIR, "ggml-cuda.dll")

    # 有 CUDA 支持且 DLL 存在
    if gpu_info["cuda_supported"] and os.path.exists(cuda_dll):
        logger.info(f"Using CUDA accelerated llama-server (GPU: {gpu_info['name']})")
        return cuda_exe, {"use_gpu": True, "ngl": 99}

    # 回退 CPU
    logger.info("Using CPU llama-server")
    return cpu_exe, {"use_gpu": False, "ngl": 0}


def find_model(data_dir=None):
    """查找 GGUF 模型文件"""
    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(LLAMA_DIR), "..", "data")
    model_dir = os.path.join(os.path.abspath(data_dir), "models")

    if not os.path.isdir(model_dir):
        logger.warning(f"Model directory not found: {model_dir}")
        return None

    for f in os.listdir(model_dir):
        if f.endswith(".gguf"):
            path = os.path.join(model_dir, f)
            logger.info(f"Found model: {f} ({os.path.getsize(path)//1024//1024} MB)")
            return path

    logger.warning(f"No GGUF model found in {model_dir}")
    return None


def start_llama_server(model_path, gpu_info=None, port=8080, context_size=2048):
    """启动 llama-server 子进程"""
    exe_path, opts = get_llama_server_path(gpu_info)
    ngl = opts["ngl"]

    if not model_path or not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path}")

    cmd = [
        exe_path,
        "-m", model_path,
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(context_size),
        "--temp", "0.3",
        "--top-p", "0.7",
    ]
    if ngl > 0:
        cmd.extend(["-ngl", str(ngl)])

    logger.info(f"Starting llama-server: {' '.join(cmd)}")
    log_file = open(os.path.join(LLAMA_DIR, "server.log"), "w")
    proc = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT,
        env={**os.environ, "PATH": f"{LLAMA_DIR};{os.environ.get('PATH', '')}"}
    )
    return proc


def wait_for_server(port=8080, timeout=60):
    """等待 llama-server HTTP 就绪"""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status == 200:
                elapsed = time.time() - start
                logger.info(f"llama-server ready after {elapsed:.0f}s")
                return True
        except:
            time.sleep(1)
    logger.error(f"llama-server not ready after {timeout}s")
    return False


def test_llama_server(port=8080):
    """简单测试 server 响应"""
    import urllib.request, json
    try:
        body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 10, "temperature": 0.3
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        logger.info(f"llama-server test OK: {content[:50]}")
        return True
    except Exception as e:
        logger.error(f"llama-server test failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    gpu = detect_gpu()
    print(f"GPU: {gpu}")
    exe, opts = get_llama_server_path(gpu)
    print(f"Server: {exe}")
    print(f"Options: {opts}")
    model = find_model()
    if model:
        print(f"Model: {model}")
