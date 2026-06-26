# QueueStorm Investigator API Backend

AI-powered support ticket analysis system for a digital finance platform. This repository contains the backend HTTP API service skeleton.

## Technology Stack
- **Language**: Python 3.11+
- **Web Framework**: FastAPI
- **Validation**: Pydantic v2
- **ASGI Server**: Uvicorn
- **Testing**: Pytest / HTTPX

---

## Local Setup

### Prerequisites
- Python 3.11+
- Virtual environment tool (e.g. `venv`)

### Installation
1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # On Linux/macOS:
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create your `.env` file from the example:
   ```bash
   copy .env.example .env
   ```

### Running Locally
To start the FastAPI development server:
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
- **API Docs (Swagger UI)**: http://localhost:8000/docs
- **Health Check**: `GET http://localhost:8000/health`
- **Analyze Ticket**: `POST http://localhost:8000/analyze-ticket`

---

## Running with Docker

### Docker Build & Run
To run the server inside a Docker container:
```bash
docker build -t queuestorm-api .
docker run -p 8000:8000 --env-file .env queuestorm-api
```

### Docker Compose
Alternatively, run with docker-compose:
```bash
docker-compose up --build
```

---

## Testing

### Run Sample Cases Runner
To test the API against all 10 sample cases, first ensure the API is running locally or in Docker at `http://localhost:8000`, then run:
```bash
python tests/test_sample_cases.py
```

Or using `pytest`:
```bash
pytest tests/test_sample_cases.py
```
