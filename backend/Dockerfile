FROM python:3.12-slim

# ffmpeg/ffprobe：会议录音转写链路依赖——浏览器录音（webm/opus）转 16k 单声道 wav、
# silencedetect 静音切分、读取时长均依赖它
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
COPY . .

EXPOSE 3315
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3315"]
