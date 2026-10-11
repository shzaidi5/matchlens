FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY matchlens ./matchlens
COPY web ./web
COPY data ./data
COPY schemas ./schemas
EXPOSE 8000
CMD ["python", "-m", "matchlens", "serve", "--host", "0.0.0.0", "--port", "8000"]
