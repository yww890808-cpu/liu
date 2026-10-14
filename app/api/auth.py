from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services.auth import authenticate, create_token, login_throttle

router = APIRouter(prefix="/api/auth", tags=["登录"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenOut)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    keys = (f"user:{form.username}", f"ip:{request.client.host if request.client else '-'}")
    if login_throttle.blocked(*keys):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "密码错误次数过多，请 15 分钟后再试")
    user = authenticate(db, form.username, form.password)
    if user is None:
        login_throttle.fail(*keys)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    login_throttle.success(*keys)
    return TokenOut(access_token=create_token(user))


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username}
