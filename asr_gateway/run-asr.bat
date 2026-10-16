@echo off
cd /d C:\plm-asr
set "PATH=C:\ProgramData\chocolatey\bin;%PATH%"
REM CUDA DLLs from pip nvidia-*-cu12 packages
set "PATH=C:\plm-asr\.venv\Lib\site-packages\nvidia\cublas\bin;%PATH%"
set "PATH=C:\plm-asr\.venv\Lib\site-packages\nvidia\cuda_runtime\bin;%PATH%"
set "PATH=C:\plm-asr\.venv\Lib\site-packages\nvidia\cudnn\bin;%PATH%"
set ASR_API_KEY=de3681201c03b66d71c8956886c29ab0
set ASR_MODEL=medium
set ASR_LANGUAGE=zh
set ASR_DEVICE=cuda
set ASR_COMPUTE_TYPE=float16
set ASR_PRELOAD=1
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1
set PYTHONUNBUFFERED=1
echo ===== ASR start %date% %time% =====>> C:\plm-asr\asr.log
where ffmpeg >> C:\plm-asr\asr.log 2>&1
.venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8090 >> C:\plm-asr\asr.log 2>&1
