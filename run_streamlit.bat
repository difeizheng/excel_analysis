@echo off
chcp 65001 >nul
title excel_analysis - Port 8563

:: 切换到脚本所在的目录（确保 main.py 路径正确）
cd /d "%~dp0"

:: 检查 main.py 是否存在
if not exist "smart_qa/app.py" (
    echo 错误：未找到 app.py 文件，请确保脚本与 app.py 在同一目录下。
    pause
    exit /b 1
)

:: 方式一：直接使用 streamlit 命令（需要已安装 streamlit 并加入 PATH）
streamlit run smart_qa/app.py --server.port=8563

:: 如果上述命令失败，可以尝试使用 python -m 方式运行（取消下面一行的注释，并注释上面一行）
:: python -m streamlit run main.py --server.port=8520

:: 如果运行报错，提示未找到 streamlit，请先执行：pip install streamlit

pause