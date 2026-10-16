# PLM Meeting ASR Gateway（Windows GPU 手工部署）

> **运维/发布技能**：`.claude/skills/plm-windows-gpu-deploy/`（网络拓扑 + 通用发布；本 README 为包内说明）。

目标机生产 IP：`172.16.17.66`；管理 IP：`192.168.201.222`。  
PLM 应用机：`172.16.25.191` → `http://172.16.17.66:8090`

## 1. 前置条件

- Windows 10/11 + NVIDIA 驱动（`nvidia-smi` 可用）
- Python 3.11 或 3.12
- **ffmpeg（必装）**：会议录音多为 webm/opus，无 ffmpeg 会转写失败或卡住

```powershell
choco install ffmpeg -y
where.exe ffmpeg
ffmpeg -version
```

- 防火墙入站放通 **TCP 8090**（建议仅允许 `172.16.25.191`）

地址约定：Windows `192.168.201.222:8090`（WLAN）/ `172.16.17.66:8090`（有线）；PLM `172.16.25.191` 指向其一。

## 2. 安装

在 PowerShell（管理员可选）：

```powershell
cd C:\plm-asr   # 或任意目录，把本 asr_gateway 文件夹拷过来
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

# 国内拉取 Whisper 权重
$env:HF_ENDPOINT="https://hf-mirror.com"
$env:HF_HUB_DISABLE_XET="1"
huggingface-cli download Systran/faster-whisper-medium
```

复制配置：

```powershell
copy .env.example .env
# 编辑 .env：ASR_API_KEY 必须与 PLM 的 MEETING_ASR_REMOTE_API_KEY 一致
```

## 3. 启动

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn main:app --host 0.0.0.0 --port 8090
```

生产可用 [NSSM](https://nssm.cc/) 注册为 Windows 服务，工作目录指向本文件夹，启动命令：

```text
C:\plm-asr\.venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8090
```

## 4. 验收

在 **172.16.25.191** 上：

```bash
# PowerShell 请用 curl.exe（不要用 curl：会被别名成 Invoke-WebRequest，
# -s 会被误解析为 -SessionVariable）
curl.exe -sf -H "X-API-Key: <ASR_API_KEY>" http://192.168.201.222:8090/health
```

单文件转写：

```bash
curl -X POST http://172.16.17.66:8090/api/v1/transcribe \
  -H "X-API-Key: <ASR_API_KEY>" \
  -F "file=@test.wav" \
  -F "language=zh"
```

期望响应：

```json
{
  "segments": [
    {"start": 0.0, "end": 3.5, "text": "...", "confidence": 0.9, "language": "zh"}
  ]
}
```

## 5. PLM 侧配置（`.env.prod`）

```env
MEETING_ASR_MODE=remote
MEETING_ASR_REMOTE_URL=http://172.16.17.66:8090/api/v1/transcribe
MEETING_ASR_REMOTE_API_KEY=<与 ASR_API_KEY 相同>
MEETING_ASR_REMOTE_TIMEOUT=3600
MEETING_ASR_FALLBACK_LOCAL=false
MEETING_WHISPER_MODEL=medium
MEETING_WHISPER_LANGUAGE=zh
```

改完后重建/重启 PLM backend 容器。

## 6. 显存参考

| 模型 | 显存 | 说明 |
|------|------|------|
| small | ~2GB | 更快 |
| medium（默认） | ~5GB | 推荐 |
| large-v3 | ~10GB | 需 ≥12GB 卡 |

CUDA 不可用时，服务会自动回退 `cpu/int8`（慢，仅兜底）。
