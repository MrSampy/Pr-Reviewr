FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install fastapi uvicorn chromadb openai python-dotenv httpx

COPY . .

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]