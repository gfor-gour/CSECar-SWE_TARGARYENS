import json
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routes.health import router as health_router
from app.routes.analyze import router as analyze_router

app = FastAPI(
    title="QueueStorm Investigator API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Allow CORS for external calls if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(health_router)
app.include_router(analyze_router)

# --- Exception Handlers ---

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    
    # Check if the error is specifically for the 'complaint' field being empty/blank
    for error in errors:
        loc = error.get("loc", ())
        msg = error.get("msg", "")
        # Under Pydantic, field_validator ValueError has type 'value_error' or similar
        # Loc represents path e.g. ("body", "complaint")
        if len(loc) >= 2 and loc[0] == "body" and loc[1] == "complaint":
            # Check if this error is about the complaint being empty
            if "complaint field cannot be empty" in msg or "value_error" in error.get("type", ""):
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={
                        "error": "Invalid input",
                        "detail": "complaint field cannot be empty"
                    }
                )
                
    # Otherwise, treat it as a Bad Request (missing required fields or malformed types)
    # Simplify the details to be non-sensitive and easy to read
    details = []
    for error in errors:
        loc_str = " -> ".join(str(l) for l in error.get("loc", ()))
        msg = error.get("msg", "")
        details.append(f"{loc_str}: {msg}")
    
    detail_msg = "; ".join(details)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Bad request",
            "detail": f"Validation failed: {detail_msg}"
        }
    )

@app.exception_handler(json.JSONDecodeError)
async def json_decode_exception_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Bad request",
            "detail": "Malformed JSON payload in request body"
        }
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 500:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error"}
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Log the exception locally if needed (or standard error stream), but NEVER expose to user
    # Returning strict 500 Internal server error
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"}
    )
