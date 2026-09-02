#!/usr/bin/env python3
"""签发演示 JWT（本机演示/集成测试用）。

用法：python scripts/issue_token.py [--tenant tenant-001] [--out /tmp/atlas_token]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.security.jwt import create_token  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="tenant-001")
    parser.add_argument("--out", default="/tmp/atlas_token")
    args = parser.parse_args()

    settings = get_settings()
    token = create_token(args.tenant, ["retrieval:read"], settings.jwt_secret, settings.jwt_exp_hours)
    Path(args.out).write_text(token, encoding="utf-8")
    print(token)


if __name__ == "__main__":
    main()
