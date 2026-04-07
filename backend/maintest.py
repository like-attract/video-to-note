import sys
print("Python 解释器路径:", sys.executable)

try:
    import fastapi
    print("fastapi 安装路径:", fastapi.__file__)
except ImportError as e:
    print("fastapi 导入失败:", e)

try:
    import uvicorn
    print("uvicorn 安装路径:", uvicorn.__file__)
except ImportError as e:
    print("uvicorn 导入失败:", e)