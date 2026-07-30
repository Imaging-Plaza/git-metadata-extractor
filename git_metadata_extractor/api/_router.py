"""The single /v2 APIRouter — route modules attach to it."""
from fastapi import APIRouter

v2_router = APIRouter(prefix="/v2")
