import os
import shutil
import base64
import subprocess
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from pydantic import BaseModel

# 設定 logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# 加入 CORS 支援
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChunkResponse(BaseModel):
    index: int
    data: str  # Base64 string
    fileName: str
    size: int  # bytes

class SplitResponse(BaseModel):
    success: bool
    totalChunks: int
    chunks: List[ChunkResponse]
    originalSize: int
    message: str

@app.post("/split")
async def split_audio(file: UploadFile = File(...)):
    """
    接收音訊檔案，使用 FFmpeg 切割成多段，回傳 Base64 編碼的分段
    每段約 10 分鐘 (600 秒)
    """
    logger.info(f"[Split] 收到檔案: {file.filename}, Content-Type: {file.content_type}")
    
    temp_dir = "/tmp/split_task"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    # 保留原始副檔名
    original_ext = os.path.splitext(file.filename)[1] if file.filename else ".m4a"
    if not original_ext:
        original_ext = ".m4a"
    
    input_path = os.path.join(temp_dir, f"input_audio{original_ext}")
    output_pattern = os.path.join(temp_dir, f"out%03d{original_ext}")
    
    # 1. 寫入暫存檔
    try:
        content = await file.read()
        original_size = len(content)
        logger.info(f"[Split] 檔案大小: {original_size / 1024 / 1024:.2f} MB")
        
        with open(input_path, "wb") as buffer:
            buffer.write(content)
    except Exception as e:
        logger.error(f"[Split] 檔案儲存錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File save error: {str(e)}")
    
    # 2. 呼叫 FFmpeg 切割 (每 600 秒 = 10 分鐘切一段)
    cmd = [
        "ffmpeg", "-i", input_path, 
        "-f", "segment", 
        "-segment_time", "600",  # 10 分鐘
        "-c", "copy",  # 複製串流，不轉檔
        "-y",  # 覆蓋輸出檔案
        output_pattern
    ]
    
    logger.info(f"[Split] 執行 FFmpeg: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            timeout=300  # 5 分鐘超時
        )
        logger.info(f"[Split] FFmpeg 完成")
    except subprocess.TimeoutExpired:
        logger.error("[Split] FFmpeg 超時")
        shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail="FFmpeg timeout - file may be too large")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        logger.error(f"[Split] FFmpeg 錯誤: {error_msg}")
        shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {error_msg}")
    except FileNotFoundError:
        logger.error("[Split] FFmpeg 未安裝")
        shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail="FFmpeg not installed on server")
    
    # 3. 讀取切割後的檔案並轉為 Base64
    chunks = []
    files = sorted([f for f in os.listdir(temp_dir) if f.startswith("out")])
    
    logger.info(f"[Split] 產生了 {len(files)} 個分段")
    
    for idx, filename in enumerate(files):
        file_path = os.path.join(temp_dir, filename)
        with open(file_path, "rb") as f:
            content = f.read()
            b64_data = base64.b64encode(content).decode('utf-8')
            chunks.append(ChunkResponse(
                index=idx, 
                data=b64_data,
                fileName=filename,
                size=len(content)
            ))
            logger.info(f"[Split] 分段 {idx}: {filename}, {len(content) / 1024 / 1024:.2f} MB")
    
    # 清理暫存
    shutil.rmtree(temp_dir)
    
    return SplitResponse(
        success=True,
        totalChunks=len(chunks),
        chunks=chunks,
        originalSize=original_size,
        message=f"成功切割成 {len(chunks)} 段"
    )

@app.get("/")
def read_root():
    return {
        "status": "服務正在執行", 
        "tool": "FFmpeg 分割器",
        "endpoints": {
            "POST /split": "上傳音訊檔案進行切割",
            "GET /health": "健康檢查"
        }
    }

@app.get("/health")
def health_check():
    """健康檢查，確認 FFmpeg 是否可用"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], 
            capture_output=True, 
            timeout=5
        )
        ffmpeg_ok = result.returncode == 0
        ffmpeg_version = result.stdout.decode().split('\n')[0] if ffmpeg_ok else "N/A"
    except Exception as e:
        ffmpeg_ok = False
        ffmpeg_version = str(e)
    
    return {
        "status": "healthy" if ffmpeg_ok else "unhealthy",
        "ffmpeg": "available" if ffmpeg_ok else "not available",
        "ffmpeg_version": ffmpeg_version
    }
