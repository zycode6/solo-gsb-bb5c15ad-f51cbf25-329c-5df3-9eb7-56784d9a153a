FROM python:3.12-slim

WORKDIR /app

# 依赖安装纳入镜像构建，启动无需额外步骤
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 启动时先运行初始化自检（验证 NumPy 最小二乘可用），再拉起 API 服务
CMD ["sh", "-c", "python -m app.selfcheck && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
