import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.chat import router as chat_router, warmup_endpoint
from app.api.v1.export import router as export_router
from app.api.v1.attachments import router as attachments_router
from app.api.v1.voice import router as voice_router
from app.api.v1.quiz import router as quiz_router
from app.api.v1.models import router as models_router
from app.api.v1.graph_chat import router as graph_chat_router
from app.core.logging import configure_logging
from app.core.rate_limit import attach_rate_limiter

configure_logging()

app = FastAPI(
    title="AI Vidyarthi Backend",
    description="Backend API for AI Vidyarthi built with FastAPI and LangGraph",
    version="0.1.0"
)

attach_rate_limiter(app)

# CORS configuration to allow connections from your frontend (RAGNOUS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update this in production to match your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_warmup_event():
    """Background startup task to warm up embeddings & DB pool on server launch."""
    print("🔥 Starting backend warm up sequence...")
    asyncio.create_task(warmup_endpoint())

@app.get("/")
async def root():
    return {"message": "Welcome to AI Vidyarthi Backend API"}

@app.get("/api/v1/health")
@app.get("/api/v1/warmup")
async def health_and_warmup():
    return await warmup_endpoint()

app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(export_router, prefix="/api/v1/export", tags=["export"])
app.include_router(attachments_router, prefix="/api/v1/attachments", tags=["attachments"])
app.include_router(voice_router, prefix="/api/v1/voice", tags=["voice"])
app.include_router(quiz_router, prefix="/api/v1/quiz", tags=["quiz"])
# 3D meshes are streamed through, never saved — see app/api/v1/models.py.
app.include_router(models_router, prefix="/api/v1/models", tags=["models"])
# LangGraph-backed tutor. Same request/response shape as /api/v1/chat.
app.include_router(graph_chat_router, prefix="/api/v1/chat/graph", tags=["chat-graph"])
