from fastapi import APIRouter

from app.api.v1 import auth, cameras, campuses, misc, nvrs, tunnel, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(misc.router)
api_router.include_router(auth.router)
api_router.include_router(campuses.router)
api_router.include_router(nvrs.router)
api_router.include_router(cameras.router)
api_router.include_router(tunnel.router)
api_router.include_router(users.router)
