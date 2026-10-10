# Read-only Streamlit demo. Build from the repo root:
#   docker build -f deploy/Dockerfile -t cyber-risk-quant .
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY profiles ./profiles
COPY app ./app
COPY docs/METHODOLOGY.md ./docs/METHODOLOGY.md
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps -e .
RUN useradd -m appuser
USER appuser
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
