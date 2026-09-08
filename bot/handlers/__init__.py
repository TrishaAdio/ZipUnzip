from aiogram import Router

from . import archives, basic


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(basic.router)
    root.include_router(archives.router)
    return root


__all__ = ["build_router"]
