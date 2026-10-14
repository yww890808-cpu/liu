"""命令行工具。

    python -m app.cli create-user <用户名>      # 交互输入密码
"""
import argparse
import getpass
import sys

from app.db.session import SessionLocal
from app.services.auth import create_user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="创建后台登录账号")
    cu.add_argument("username")
    args = parser.parse_args(argv)

    if args.cmd == "create-user":
        password = getpass.getpass("密码：")
        if len(password) < 8:
            print("密码至少 8 位", file=sys.stderr)
            return 1
        if password != getpass.getpass("再次输入："):
            print("两次输入不一致", file=sys.stderr)
            return 1
        with SessionLocal() as db:
            create_user(db, args.username, password)
        print(f"已创建用户 {args.username}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
