"""Ozon API 统一异常。"""
from app.utils.api_client import ApiError


class OzonAPIError(ApiError):
    """调用 Ozon API 失败。status_code 为 None 表示网络层错误（超时、连接失败等）。"""
