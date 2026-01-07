import os
import shutil
import base64
import subprocess
from fastapi import FastAPI, UploadFile, File, HTTPException
from typing import List
from pydantic import BaseModel

app = FastAPI()

class ChunkResponse(BaseModel):
    index: int
    data: str  # Base64 string

@app.post("/split", response_model=List[ChunkResponse])
async def split_audio(file: UploadFile = File(...)):
    temp_dir = "/tmp/split_task"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    input_path = os.path.join(temp_dir, "input_audio")
    output_pattern = os.path.join(temp_dir, "out%03d.m4a")

    # 1. 寫入暫存檔
    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File save error: {str(e)}")

    # 2. 呼叫 FFmpeg 切割 (每 600 秒切一段，複製串流不轉檔)
    # -c copy 速度極快，不會降低音質
    cmd = [
        "ffmpeg", "-i", input_path, "-f", "segment", 
        "-segment_time", "600", "-c", "copy", output_pattern
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {str(e)}")

    # 3. 讀取切割後的檔案並轉為 Base64
    chunks = []
    files = sorted([f for f in os.listdir(temp_dir) if f.startswith("out")])
    
    for idx, filename in enumerate(files):
        with open(os.path.join(temp_dir, filename), "rb") as f:
            # 讀取二進位並轉 Base64
            b64_data = base64.b64encode(f.read()).decode('utf-8')
            chunks.append(ChunkResponse(index=idx, data=b64_data))

    # 清理暫存
    shutil.rmtree(temp_dir)
    
    return chunks

@app.get("/")
def read_root():
    return {"status": "Service is running", "tool": "FFmpeg Splitter"}