import os
import json
import asyncio
import subprocess
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI()

# 路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "web_config.json")
RESULTS_DIR = os.path.join(BASE_DIR, "数据分析/分析结果")
DB_DIR = os.path.join(BASE_DIR, "收集数据/数据库")

# 确保目录存在
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/results", StaticFiles(directory=RESULTS_DIR), name="results")
templates = Jinja2Templates(directory="templates")

process_logs = []
is_running = False


class ConfigUpdate(BaseModel):
    api_url: str
    api_key: str
    model_id: str
    total_requests: int
    max_concurrent: int
    request_delay: float
    context_length: int
    num_insertions: int
    base_pattern: str
    needle_range: str
    text_file: str
    temperature: float
    top_p: float
    frequency_penalty: float
    presence_penalty: float
    max_tokens: int


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/config")
async def get_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config.dict(), f, indent=4)
    return {"status": "success"}


async def run_command(command: list, cwd: str, env_vars: dict = None):
    global is_running, process_logs
    is_running = True

    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env
    )

    while True:
        line = await process.stdout.readline()
        if not line: break
        msg = line.decode('utf-8', errors='ignore').strip()
        process_logs.append(msg)
        if len(process_logs) > 1000: process_logs.pop(0)

    await process.wait()
    is_running = False


@app.post("/api/run_test")
async def run_test(background_tasks: BackgroundTasks):
    with open(CONFIG_PATH, 'r') as f: conf = json.load(f)

    # 构造 CLI 参数
    cmd = ["python", "run_batch_test.py", str(conf['total_requests']), str(conf['max_concurrent']),
           str(conf['request_delay']), str(conf['context_length']), str(conf['num_insertions']),
           conf['base_pattern'], conf['needle_range']]
    if conf['text_file'] != "None": cmd.append(conf['text_file'])

    env_params = {
        "API_URL": conf['api_url'], "API_KEY": conf['api_key'], "MODEL_ID": conf['model_id'],
        "LLM_TEMP": str(conf['temperature']), "LLM_TOP_P": str(conf['top_p']),
        "LLM_FREQ_P": str(conf['frequency_penalty']), "LLM_PRES_P": str(conf['presence_penalty']),
        "LLM_MAX_T": str(conf['max_tokens'])
    }

    process_logs.clear()
    background_tasks.add_task(run_command, cmd, os.path.join(BASE_DIR, "收集数据"), env_params)
    return {"status": "started"}


@app.post("/api/analyze")
async def analyze(background_tasks: BackgroundTasks):
    with open(CONFIG_PATH, 'r') as f: conf = json.load(f)
    safe_id = conf['model_id'].replace("-", "_").replace(".", "_")
    db_path = os.path.join(DB_DIR, f"{safe_id}.db")

    async def task():
        # 按序执行原项目脚本
        await run_command(["python", "analyze_summary.py", db_path], os.path.join(BASE_DIR, "数据分析"))
        await run_command(["python", "analyze_errors.py", db_path], os.path.join(BASE_DIR, "数据分析"))
        await run_command(["python", "analyze_position_accuracy.py", db_path], os.path.join(BASE_DIR, "数据分析"))
        err_db = os.path.join(RESULTS_DIR, f"error_stats_{safe_id}.db")
        await run_command(["python", "generate_all_heatmaps.py", err_db], os.path.join(BASE_DIR, "数据分析"))

    background_tasks.add_task(task)
    return {"status": "started"}


@app.get("/api/logs")
async def get_logs():
    return {"logs": process_logs, "is_running": is_running}


@app.get("/api/images")
async def get_images():
    if not os.path.exists(RESULTS_DIR): return []
    return [f for f in os.listdir(RESULTS_DIR) if f.endswith(('.png', '.jpg'))]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)