# 通知路由：通知中心（风控/跟单/榜单/系统）
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fwsort.database import get_async_db
from fwsort.models import Notification, User
from fwsort.response import success
from router.auth_router import current_user

router = APIRouter()


# ========== 1. 我的通知列表 ==========
@router.get("/list", response_model=dict)
async def list_notifications(
    only_unread: bool = False,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """当前用户的通知列表"""
    q = select(Notification).where(Notification.user_id == user.id)
    if only_unread:
        q = q.where(Notification.is_read == False)  # noqa: E712
    q = q.order_by(Notification.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return success(data={"count": len(rows), "items": [
        {
            "id": n.id,
            "ntype": n.ntype,
            "title": n.title,
            "content": n.content,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]})


# ========== 2. 标已读 ==========
@router.post("/{nid}/read", response_model=dict)
async def mark_read(
    nid: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """单条标已读"""
    n = (
        await db.execute(
            select(Notification).where(
                Notification.id == nid,
                Notification.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not n:
        return success(message="not found or not yours")
    n.is_read = True
    await db.flush()
    return success(message="read")


# ========== 3. 全部标已读（WP-10：bulk update 一次性更新）==========
@router.post("/read-all", response_model=dict)
async def mark_all_read(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> dict:
    """全部已读（WP-10：bulk update，单条 UPDATE 替换 N 次 ORM flush）"""
    # 先 count 一下用于返回
    count_stmt = select(Notification.id).where(
        Notification.user_id == user.id,
        Notification.is_read == False,  # noqa: E712
    )
    ids = (await db.execute(count_stmt)).scalars().all()
    if not ids:
        return success(data={"marked": 0}, message="all read")
    # WP-10：单条 UPDATE WHERE 替换逐条 ORM flush；1 万条场景 < 100ms
    await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    await db.flush()
    return success(data={"marked": len(ids)}, message="all read")


# ========== 4. 通知写入辅助（被其他模块调用）==========
async def push(
    db: AsyncSession,
    user_id: int,
    ntype: int,
    title: str,
    content: str = "",
) -> Notification:
    """推一条通知（内部服务调用，不走 HTTP）"""
    n = Notification(user_id=user_id, ntype=ntype, title=title, content=content)
    db.add(n)
    return n
