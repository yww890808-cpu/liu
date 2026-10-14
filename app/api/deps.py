"""接口公共依赖：数据库会话、当前登录用户。"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db.models import Shop, User
from app.db.session import get_db
from app.services.auth import user_from_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def get_shop(shop_id: int, db: Session = Depends(get_db)) -> Shop:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "店铺不存在")
    return shop
